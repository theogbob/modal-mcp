"""
modal-mcp-server: A modern MCP server for Modal, designed for Claude Code.

Provides tools for:
- Deploying Modal apps
- Managing Modal volumes (list, browse, upload, download, delete)
- Running code in Modal sandboxes
- Listing deployed apps
"""

import json
import os
import re
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
    cmd = ["modal", *args]
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
    duration = min(max(duration, 3), 60)
    args = ["app", "logs", app_name_or_id]
    if environment:
        args.extend(["--env", environment])

    cmd = ["modal", *args]
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

        return f"{out}\n\n(Captured {duration}s of log stream)" if out else f"No logs captured in {duration}s."

    except FileNotFoundError:
        return "Error: Modal CLI not found."


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
# Secrets / environments / profile
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
def current_profile() -> str:
    """Show the current Modal profile (workspace/user info)."""
    _, text = _run("profile", "current")
    return text


@mcp.tool()
def list_environments() -> str:
    """List all Modal environments in the current workspace."""
    _, text = _run("environment", "list")
    return text


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
