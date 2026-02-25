"""
modal-mcp-server: A comprehensive MCP server for Modal, designed for Claude Code.

Provides tools for:
- App management (deploy, run, list, stop, logs)
- Container management (list, logs, stop)
- Volume management (list, browse, create, delete, rename, upload, download, remove)
- Sandbox execution (shell commands, Python code, with GPU support)
- Secret management (list, create, delete)
- Queue management (list, create, delete, clear, peek, length)
- Dict management (list, create, delete, clear, get, items)
- Environment management (list, create, delete)
- Billing & workspace info (usage reports, profile, token info)
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("modal-mcp-server")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODAL_ENV = {**os.environ, "TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "200"}

# Resolve the modal binary path once — uvx/venv may not have it on PATH for Popen
_MODAL_BIN = shutil.which("modal", path=_MODAL_ENV.get("PATH")) or "modal"


def _strip_rich(text: str) -> str:
    """Strip Rich box-drawing characters and ANSI codes."""
    text = re.sub(r'[\u2500-\u257f]', '', text)
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    text = re.sub(r' {2,}', ' ', text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return '\n'.join(lines)


def _run(*args: str, timeout: int = 120) -> tuple[bool, str]:
    """Run a modal CLI command. Returns (success, clean_text)."""
    cmd = [_MODAL_BIN, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_MODAL_ENV)
        out = r.stdout.strip()
        err = _strip_rich(r.stderr.strip()) if r.stderr else ""
        if r.returncode == 0:
            return True, out if out else (err or "OK")
        return False, f"Error: {err}" if err else f"Command failed (exit {r.returncode})"
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return False, "Modal CLI not found. Install: pip install modal && python3 -m modal setup"


def _run_json(*args: str, timeout: int = 120) -> str:
    """Run modal CLI with --json, return readable formatted text."""
    ok, text = _run(*args, "--json", timeout=timeout)
    if not ok:
        return text
    try:
        data = json.loads(text)
        if isinstance(data, list):
            if not data:
                return "No results."
            lines = []
            for item in data:
                lines.append('\n'.join(f"  {k}: {v}" for k, v in item.items()))
            return '\n\n'.join(lines)
        elif isinstance(data, dict):
            return '\n'.join(f"{k}: {v}" for k, v in data.items())
        return str(data)
    except (json.JSONDecodeError, TypeError):
        return text


def _streaming_capture(*args: str, duration: int = 10) -> str:
    """Run a streaming modal CLI command, capture output for a fixed duration."""
    duration = min(max(duration, 3), 60)
    cmd = [_MODAL_BIN, *args]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_MODAL_ENV)
        try:
            stdout, stderr = proc.communicate(timeout=duration)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()

        out = _strip_rich(stdout) if stdout else ""
        err = _strip_rich(stderr) if stderr else ""

        if err and not out:
            return f"Error: {err}"

        if len(out) > 30000:
            head, tail = out[:5000], out[-25000:]
            out = f"{head}\n\n... [truncated middle] ...\n\n{tail}"

        return f"{out}\n\n(Captured {duration}s of log stream)" if out else f"No output captured in {duration}s."

    except FileNotFoundError:
        return "Error: Modal CLI not found."


# ---------------------------------------------------------------------------
# App management
# ---------------------------------------------------------------------------

@mcp.tool()
def deploy_app(app_path: str, name: Optional[str] = None, environment: Optional[str] = None) -> str:
    """Deploy a Modal application from a Python file.

    Args:
        app_path: Absolute path to the Modal application file (e.g. /home/user/my_app.py)
        name: Optional name for the deployment
        environment: Optional Modal environment to deploy to
    """
    path = Path(app_path)
    if not path.is_absolute():
        return f"Error: Path must be absolute, got: {app_path}"
    if not path.exists():
        return f"Error: File not found: {app_path}"
    if path.suffix != ".py":
        return f"Error: Expected a .py file, got: {path.suffix}"

    args = ["deploy", str(path)]
    if name:
        args.extend(["--name", name])
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args, timeout=300)
    return text


@mcp.tool()
def run_app(app_path: str, environment: Optional[str] = None) -> str:
    """Run a Modal application (one-off execution, not a persistent deployment).

    Args:
        app_path: Absolute path to the Modal application file
        environment: Optional Modal environment
    """
    path = Path(app_path)
    if not path.is_absolute():
        return f"Error: Path must be absolute, got: {app_path}"
    if not path.exists():
        return f"Error: File not found: {app_path}"

    args = ["run", str(path)]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args, timeout=300)
    return text


@mcp.tool()
def list_apps(environment: Optional[str] = None) -> str:
    """List all deployed Modal apps.

    Args:
        environment: Optional Modal environment to filter by
    """
    args = ["app", "list"]
    if environment:
        args.extend(["--env", environment])
    return _run_json(*args)


@mcp.tool()
def stop_app(app_name: str, environment: Optional[str] = None) -> str:
    """Stop a deployed Modal app.

    Args:
        app_name: Name of the app to stop
        environment: Optional Modal environment
    """
    args = ["app", "stop", app_name]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def app_logs(app_name_or_id: str, duration: int = 10, environment: Optional[str] = None) -> str:
    """Get recent logs for a deployed Modal app.

    Streams are captured for a fixed duration then returned.

    Args:
        app_name_or_id: Name or App ID (e.g. "ap-xxxx") of the app
        duration: Seconds to capture (default: 10, max: 60)
        environment: Optional Modal environment
    """
    args = ["app", "logs", app_name_or_id]
    if environment:
        args.extend(["--env", environment])
    return _streaming_capture(*args, duration=duration)


# ---------------------------------------------------------------------------
# Container management
# ---------------------------------------------------------------------------

@mcp.tool()
def list_containers(environment: Optional[str] = None) -> str:
    """List all currently running Modal containers.

    Args:
        environment: Optional Modal environment to filter by
    """
    args = ["container", "list"]
    if environment:
        args.extend(["--env", environment])
    return _run_json(*args)


@mcp.tool()
def container_logs(container_id: str, duration: int = 10) -> str:
    """Get logs for a specific running Modal container.

    Args:
        container_id: Container ID (from list_containers)
        duration: Seconds to capture (default: 10, max: 60)
    """
    return _streaming_capture("container", "logs", container_id, duration=duration)


@mcp.tool()
def stop_container(container_id: str) -> str:
    """Stop a running Modal container and reassign its in-progress inputs.

    Args:
        container_id: Container ID to stop
    """
    _, text = _run("container", "stop", container_id)
    return text


# ---------------------------------------------------------------------------
# Volume management
# ---------------------------------------------------------------------------

@mcp.tool()
def list_volumes(environment: Optional[str] = None) -> str:
    """List all Modal volumes.

    Args:
        environment: Optional Modal environment to filter by
    """
    args = ["volume", "list"]
    if environment:
        args.extend(["--env", environment])
    return _run_json(*args)


@mcp.tool()
def list_volume_contents(volume_name: str, path: str = "/", environment: Optional[str] = None) -> str:
    """List files and directories in a Modal volume.

    Args:
        volume_name: Name of the Modal volume
        path: Path within the volume (default: "/")
        environment: Optional Modal environment
    """
    args = ["volume", "ls", volume_name, path]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def create_volume(volume_name: str, environment: Optional[str] = None) -> str:
    """Create a new Modal volume.

    Args:
        volume_name: Name for the new volume
        environment: Optional Modal environment
    """
    args = ["volume", "create", volume_name]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def delete_volume(volume_name: str, confirm: bool = False, environment: Optional[str] = None) -> str:
    """Delete a Modal volume. Requires confirm=True to proceed.

    Args:
        volume_name: Name of the volume to delete
        confirm: Must be True to actually delete (safety check)
        environment: Optional Modal environment
    """
    if not confirm:
        return "Safety check: set confirm=True to actually delete the volume. This cannot be undone."

    args = ["volume", "delete", volume_name, "--yes"]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def rename_volume(volume_name: str, new_name: str, environment: Optional[str] = None) -> str:
    """Rename a Modal volume.

    Args:
        volume_name: Current name of the volume
        new_name: New name for the volume
        environment: Optional Modal environment
    """
    args = ["volume", "rename", volume_name, new_name]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def upload_to_volume(volume_name: str, local_path: str, remote_path: str = "/", force: bool = False, environment: Optional[str] = None) -> str:
    """Upload a local file or directory to a Modal volume.

    Args:
        volume_name: Name of the Modal volume
        local_path: Path to the local file or directory
        remote_path: Destination path in the volume (default: "/")
        force: Overwrite existing files if True
        environment: Optional Modal environment
    """
    if not Path(local_path).exists():
        return f"Error: Local path not found: {local_path}"

    args = ["volume", "put", volume_name, local_path, remote_path]
    if force:
        args.append("--force")
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def download_from_volume(volume_name: str, remote_path: str, local_path: str = ".", force: bool = False, environment: Optional[str] = None) -> str:
    """Download files from a Modal volume to local disk.

    Args:
        volume_name: Name of the Modal volume
        remote_path: Path within the volume to download
        local_path: Local destination (default: current directory)
        force: Overwrite existing local files if True
        environment: Optional Modal environment
    """
    args = ["volume", "get", volume_name, remote_path, local_path]
    if force:
        args.append("--force")
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def remove_volume_file(volume_name: str, remote_path: str, recursive: bool = False, environment: Optional[str] = None) -> str:
    """Delete a file or directory from a Modal volume.

    Args:
        volume_name: Name of the Modal volume
        remote_path: Path to the file or directory to delete
        recursive: Delete directories recursively if True
        environment: Optional Modal environment
    """
    args = ["volume", "rm", volume_name, remote_path]
    if recursive:
        args.append("-r")
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


# ---------------------------------------------------------------------------
# Sandboxes
# ---------------------------------------------------------------------------

@mcp.tool()
def run_sandbox_command(
    command: str,
    image: str = "debian_slim",
    python_version: str = "3.12",
    pip_packages: Optional[list[str]] = None,
    timeout: int = 120,
    gpu: Optional[str] = None,
    environment: Optional[str] = None,
) -> str:
    """Run a command in a Modal sandbox (ephemeral cloud container).

    Args:
        command: Shell command to execute (e.g. "python -c 'print(1+1)'" or "ls /")
        image: Base image - "debian_slim" (default) or "ubuntu"
        python_version: Python version (default: "3.12")
        pip_packages: Optional pip packages to install (e.g. ["numpy", "pandas"])
        timeout: Max seconds (default: 120)
        gpu: Optional GPU type (e.g. "T4", "A10G", "A100", "H100")
        environment: Optional Modal environment
    """
    pip_install = ""
    if pip_packages:
        pkgs = ", ".join(f'"{p}"' for p in pip_packages)
        pip_install = f'.pip_install([{pkgs}])'

    gpu_arg = f', gpu="{gpu}"' if gpu else ""
    env_arg = f', environment_name="{environment}"' if environment else ""

    script = f'''
import modal

image = modal.Image.{image}(python_version="{python_version}"){pip_install}

sb = modal.Sandbox.create(
    "bash", "-c", """{command}""",
    image=image,
    timeout={timeout}{gpu_arg}{env_arg},
)
sb.wait()
stdout = sb.stdout.read()
stderr = sb.stderr.read()
rc = sb.returncode

print("===STDOUT===")
print(stdout)
print("===STDERR===")
print(stderr)
print(f"===RC={{rc}}===")
'''

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script)
        tmp_path = f.name

    try:
        r = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True,
            timeout=timeout + 60, env=_MODAL_ENV,
        )

        output = r.stdout
        sb_out = sb_err = ""
        if "===STDOUT===" in output:
            sb_out = output.split("===STDOUT===")[1].split("===STDERR===")[0].strip()
        if "===STDERR===" in output:
            sb_err = output.split("===STDERR===")[1].split("===RC===")[0].strip()

        lines = []
        if sb_out:
            lines.append(sb_out)
        if sb_err:
            lines.append(f"Stderr: {sb_err}")
        if r.stderr and r.stderr.strip():
            lines.append(f"Runner: {_strip_rich(r.stderr.strip())}")

        return '\n'.join(lines) if lines else "Sandbox completed with no output."

    except subprocess.TimeoutExpired:
        return f"Error: Sandbox timed out after {timeout + 60}s"
    finally:
        os.unlink(tmp_path)


@mcp.tool()
def run_python_in_sandbox(
    code: str,
    pip_packages: Optional[list[str]] = None,
    python_version: str = "3.12",
    timeout: int = 120,
    gpu: Optional[str] = None,
) -> str:
    """Run Python code in a Modal sandbox.

    Args:
        code: Python code to execute
        pip_packages: Optional pip packages to install
        python_version: Python version (default: "3.12")
        timeout: Max seconds (default: 120)
        gpu: Optional GPU type (e.g. "T4", "A10G", "A100", "H100")
    """
    return run_sandbox_command(
        command=f"python3 -c {json.dumps(code)}",
        pip_packages=pip_packages,
        python_version=python_version,
        timeout=timeout,
        gpu=gpu,
    )


# ---------------------------------------------------------------------------
# Secret management
# ---------------------------------------------------------------------------

@mcp.tool()
def list_secrets(environment: Optional[str] = None) -> str:
    """List all Modal secrets.

    Args:
        environment: Optional Modal environment
    """
    args = ["secret", "list"]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def create_secret(secret_name: str, key_values: dict[str, str], environment: Optional[str] = None) -> str:
    """Create a new Modal secret with key-value pairs.

    Args:
        secret_name: Name for the secret (e.g. "my-api-keys")
        key_values: Dict of key-value pairs (e.g. {"API_KEY": "sk-xxx", "DB_URL": "postgres://..."})
        environment: Optional Modal environment
    """
    if not key_values:
        return "Error: key_values must contain at least one key-value pair."

    args = ["secret", "create", secret_name]
    for k, v in key_values.items():
        args.append(f"{k}={v}")
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def delete_secret(secret_name: str, confirm: bool = False, environment: Optional[str] = None) -> str:
    """Delete a Modal secret. Requires confirm=True.

    Args:
        secret_name: Name of the secret to delete
        confirm: Must be True to actually delete (safety check)
        environment: Optional Modal environment
    """
    if not confirm:
        return "Safety check: set confirm=True to actually delete the secret. This cannot be undone."

    args = ["secret", "delete", secret_name, "--yes"]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

@mcp.tool()
def list_queues(environment: Optional[str] = None) -> str:
    """List all Modal queues.

    Args:
        environment: Optional Modal environment
    """
    args = ["queue", "list"]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def create_queue(queue_name: str, environment: Optional[str] = None) -> str:
    """Create a new Modal queue.

    Args:
        queue_name: Name for the queue
        environment: Optional Modal environment
    """
    args = ["queue", "create", queue_name]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def delete_queue(queue_name: str, confirm: bool = False, environment: Optional[str] = None) -> str:
    """Delete a Modal queue. Requires confirm=True.

    Args:
        queue_name: Name of the queue to delete
        confirm: Must be True to actually delete (safety check)
        environment: Optional Modal environment
    """
    if not confirm:
        return "Safety check: set confirm=True to actually delete the queue."

    args = ["queue", "delete", queue_name, "--yes"]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def clear_queue(queue_name: str, partition: Optional[str] = None, environment: Optional[str] = None) -> str:
    """Clear all items from a Modal queue.

    Args:
        queue_name: Name of the queue to clear
        partition: Optional partition name (clears default partition if not set)
        environment: Optional Modal environment
    """
    args = ["queue", "clear", queue_name]
    if partition:
        args.extend(["-p", partition])
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def peek_queue(queue_name: str, n: int = 5, partition: Optional[str] = None, environment: Optional[str] = None) -> str:
    """Peek at the next N items in a Modal queue without removing them.

    Args:
        queue_name: Name of the queue
        n: Number of items to peek at (default: 5)
        partition: Optional partition name
        environment: Optional Modal environment
    """
    args = ["queue", "peek", queue_name, str(n)]
    if partition:
        args.extend(["-p", partition])
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def queue_length(queue_name: str, partition: Optional[str] = None, total: bool = False, environment: Optional[str] = None) -> str:
    """Get the length of a Modal queue.

    Args:
        queue_name: Name of the queue
        partition: Optional partition name
        total: If True, sum across all partitions
        environment: Optional Modal environment
    """
    args = ["queue", "len", queue_name]
    if partition:
        args.extend(["-p", partition])
    if total:
        args.append("-t")
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


# ---------------------------------------------------------------------------
# Dict management
# ---------------------------------------------------------------------------

@mcp.tool()
def list_dicts(environment: Optional[str] = None) -> str:
    """List all Modal dicts.

    Args:
        environment: Optional Modal environment
    """
    args = ["dict", "list"]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def create_dict(dict_name: str, environment: Optional[str] = None) -> str:
    """Create a new Modal dict.

    Args:
        dict_name: Name for the dict
        environment: Optional Modal environment
    """
    args = ["dict", "create", dict_name]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def delete_dict(dict_name: str, confirm: bool = False, environment: Optional[str] = None) -> str:
    """Delete a Modal dict. Requires confirm=True.

    Args:
        dict_name: Name of the dict to delete
        confirm: Must be True to actually delete (safety check)
        environment: Optional Modal environment
    """
    if not confirm:
        return "Safety check: set confirm=True to actually delete the dict."

    args = ["dict", "delete", dict_name, "--yes"]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def clear_dict(dict_name: str, environment: Optional[str] = None) -> str:
    """Clear all entries from a Modal dict.

    Args:
        dict_name: Name of the dict to clear
        environment: Optional Modal environment
    """
    args = ["dict", "clear", dict_name]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def get_dict_value(dict_name: str, key: str, environment: Optional[str] = None) -> str:
    """Get a value from a Modal dict by key.

    Args:
        dict_name: Name of the dict
        key: Key to look up
        environment: Optional Modal environment
    """
    args = ["dict", "get", dict_name, key]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


@mcp.tool()
def list_dict_items(dict_name: str, n: int = 20, environment: Optional[str] = None) -> str:
    """List items in a Modal dict.

    Args:
        dict_name: Name of the dict
        n: Max number of items to show (default: 20)
        environment: Optional Modal environment
    """
    args = ["dict", "items", dict_name, str(n)]
    if environment:
        args.extend(["--env", environment])
    _, text = _run(*args)
    return text


# ---------------------------------------------------------------------------
# Environment management
# ---------------------------------------------------------------------------

@mcp.tool()
def list_environments() -> str:
    """List all Modal environments in the current workspace."""
    _, text = _run("environment", "list")
    return text


@mcp.tool()
def create_environment(env_name: str) -> str:
    """Create a new Modal environment.

    Args:
        env_name: Name for the new environment (e.g. "staging", "production")
    """
    _, text = _run("environment", "create", env_name)
    return text


@mcp.tool()
def delete_environment(env_name: str, confirm: bool = False) -> str:
    """Delete a Modal environment. Requires confirm=True.

    Args:
        env_name: Name of the environment to delete
        confirm: Must be True to actually delete (safety check)
    """
    if not confirm:
        return "Safety check: set confirm=True to actually delete the environment. All resources in it will be deleted."

    _, text = _run("environment", "delete", env_name, "--yes")
    return text


# ---------------------------------------------------------------------------
# Profile & token
# ---------------------------------------------------------------------------

@mcp.tool()
def current_profile() -> str:
    """Show the current Modal profile (workspace/user info)."""
    _, text = _run("profile", "current")
    return text


@mcp.tool()
def token_info() -> str:
    """Show info about the current Modal token/credentials."""
    _, text = _run("token", "info")
    return text


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

@mcp.tool()
def billing_usage(period: str = "this month", resolution: str = "d") -> str:
    """Check Modal workspace billing usage and spend.

    Args:
        period: Time range - "today", "yesterday", "this week", "last week", "this month", "last month"
        resolution: "d" for daily or "h" for hourly breakdown
    """
    args = ["billing", "report", "--for", period, "-r", resolution]
    _, text = _run(*args, timeout=30)
    return text


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
