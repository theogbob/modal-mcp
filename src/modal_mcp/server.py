"""
modal-mcp-server: A modern MCP server for Modal, designed for Claude Code.

Provides tools for:
- Deploying Modal apps
- Managing Modal volumes (list, browse, upload, download, delete)
- Running code in Modal sandboxes
- Listing deployed apps
"""

import asyncio
import json
import os
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

# Modal CLI uses Rich for fancy box-drawing output. Force plain text.
_MODAL_ENV = {**os.environ, "TERM": "dumb", "NO_COLOR": "1", "COLUMNS": "200"}


def _strip_rich(text: str) -> str:
    """Strip Rich box-drawing characters and clean up Modal CLI output."""
    import re
    # Remove box-drawing characters (U+2500-U+257F)
    text = re.sub(r'[\u2500-\u257f]', '', text)
    # Remove ANSI escape codes
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    # Collapse multiple spaces/blank lines
    text = re.sub(r' {2,}', ' ', text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return '\n'.join(lines)


def _run_modal_cli(*args: str, timeout: int = 120) -> dict:
    """Run a modal CLI command and return structured output."""
    cmd = ["modal", *args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_MODAL_ENV,
        )
        out = result.stdout.strip()
        err = _strip_rich(result.stderr.strip()) if result.stderr else ""

        resp = {"success": result.returncode == 0}
        if out:
            resp["output"] = out
        if err:
            resp["error" if result.returncode != 0 else "warnings"] = err
        return resp

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {timeout}s"}
    except FileNotFoundError:
        return {"success": False, "error": "Modal CLI not found. Install with: pip install modal && python3 -m modal setup"}


def _run_modal_cli_json(*args: str, timeout: int = 120) -> dict:
    """Run a modal CLI command that outputs JSON."""
    result = _run_modal_cli(*args, "--json", timeout=timeout)
    if result["success"] and result.get("output"):
        try:
            result["data"] = json.loads(result["output"])
            del result["output"]  # don't duplicate raw JSON
        except json.JSONDecodeError:
            pass
    return result


# ---------------------------------------------------------------------------
# App management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def deploy_app(app_path: str, name: Optional[str] = None, environment: Optional[str] = None) -> str:
    """Deploy a Modal application from a Python file.

    Args:
        app_path: Absolute path to the Modal application file (e.g. /home/user/my_app.py)
        name: Optional name for the deployment (overrides the app name in the file)
        environment: Optional Modal environment to deploy to
    """
    path = Path(app_path)
    if not path.is_absolute():
        return json.dumps({"success": False, "error": f"Path must be absolute, got: {app_path}"})
    if not path.exists():
        return json.dumps({"success": False, "error": f"File not found: {app_path}"})
    if not path.suffix == ".py":
        return json.dumps({"success": False, "error": f"Expected a .py file, got: {path.suffix}"})

    args = ["deploy", str(path)]
    if name:
        args.extend(["--name", name])
    if environment:
        args.extend(["--env", environment])

    result = _run_modal_cli(*args, timeout=300)
    return json.dumps(result, indent=2)


@mcp.tool()
def run_app(app_path: str, environment: Optional[str] = None) -> str:
    """Run a Modal application (one-off execution, not a persistent deployment).

    Args:
        app_path: Absolute path to the Modal application file
        environment: Optional Modal environment
    """
    path = Path(app_path)
    if not path.is_absolute():
        return json.dumps({"success": False, "error": f"Path must be absolute, got: {app_path}"})
    if not path.exists():
        return json.dumps({"success": False, "error": f"File not found: {app_path}"})

    args = ["run", str(path)]
    if environment:
        args.extend(["--env", environment])

    result = _run_modal_cli(*args, timeout=300)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_apps(environment: Optional[str] = None) -> str:
    """List all deployed Modal apps.

    Args:
        environment: Optional Modal environment to filter by
    """
    args = ["app", "list"]
    if environment:
        args.extend(["--env", environment])

    result = _run_modal_cli_json(*args)
    return json.dumps(result, indent=2)


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

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


@mcp.tool()
def app_logs(app_name_or_id: str, duration: int = 10, environment: Optional[str] = None) -> str:
    """Get recent logs for a deployed Modal app.

    Since `modal app logs` streams continuously, this captures logs for
    a fixed duration and returns what was collected.

    Args:
        app_name_or_id: Name or App ID (e.g. "ap-xxxx") of the app
        duration: Seconds to capture logs for (default: 10, max: 60)
        environment: Optional Modal environment
    """
    duration = min(max(duration, 3), 60)
    args = ["app", "logs", app_name_or_id]
    if environment:
        args.extend(["--env", environment])

    cmd = ["modal", *args]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_MODAL_ENV,
        )
        try:
            stdout, stderr = proc.communicate(timeout=duration)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()

        out = _strip_rich(stdout) if stdout else ""
        err = _strip_rich(stderr) if stderr else ""

        if err and not out:
            return json.dumps({"success": False, "error": err}, indent=2)

        resp = {"success": True, "logs": out}
        if err:
            resp["warnings"] = err
        resp["note"] = f"Captured {duration}s of log stream."
        return json.dumps(resp, indent=2)

    except FileNotFoundError:
        return json.dumps({"success": False, "error": "Modal CLI not found."}, indent=2)


# ---------------------------------------------------------------------------
# Volume tools
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

    result = _run_modal_cli_json(*args)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_volume_contents(volume_name: str, path: str = "/", environment: Optional[str] = None) -> str:
    """List files and directories in a Modal volume.

    Args:
        volume_name: Name of the Modal volume
        path: Path within the volume (default: root "/")
        environment: Optional Modal environment
    """
    args = ["volume", "ls", volume_name, path]
    if environment:
        args.extend(["--env", environment])

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


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

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


@mcp.tool()
def delete_volume(volume_name: str, confirm: bool = False, environment: Optional[str] = None) -> str:
    """Delete a Modal volume. Requires confirm=True to proceed.

    Args:
        volume_name: Name of the volume to delete
        confirm: Must be True to actually delete (safety check)
        environment: Optional Modal environment
    """
    if not confirm:
        return json.dumps({
            "success": False,
            "error": "Safety check: set confirm=True to actually delete the volume. This cannot be undone.",
        })

    args = ["volume", "delete", volume_name, "--yes"]
    if environment:
        args.extend(["--env", environment])

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


@mcp.tool()
def upload_to_volume(volume_name: str, local_path: str, remote_path: str = "/", force: bool = False, environment: Optional[str] = None) -> str:
    """Upload a local file or directory to a Modal volume.

    Args:
        volume_name: Name of the Modal volume
        local_path: Path to the local file or directory to upload
        remote_path: Destination path in the volume (default: "/")
        force: Overwrite existing files if True
        environment: Optional Modal environment
    """
    if not Path(local_path).exists():
        return json.dumps({"success": False, "error": f"Local path not found: {local_path}"})

    args = ["volume", "put", volume_name, local_path, remote_path]
    if force:
        args.append("--force")
    if environment:
        args.extend(["--env", environment])

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


@mcp.tool()
def download_from_volume(volume_name: str, remote_path: str, local_path: str = ".", force: bool = False, environment: Optional[str] = None) -> str:
    """Download files from a Modal volume to a local path.

    Args:
        volume_name: Name of the Modal volume
        remote_path: Path within the volume to download
        local_path: Local destination path (default: current directory)
        force: Overwrite existing local files if True
        environment: Optional Modal environment
    """
    args = ["volume", "get", volume_name, remote_path, local_path]
    if force:
        args.append("--force")
    if environment:
        args.extend(["--env", environment])

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


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

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Sandbox tools
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
    """Run a command in a Modal sandbox (ephemeral container).

    This creates a temporary sandbox, runs the command, and returns the output.
    Useful for running arbitrary code in the cloud without deploying an app.

    Args:
        command: Shell command to execute (e.g. "python -c 'print(1+1)'" or "ls /")
        image: Base image - "debian_slim" (default) or "ubuntu"
        python_version: Python version for the image (default: "3.12")
        pip_packages: Optional list of pip packages to install (e.g. ["numpy", "pandas"])
        timeout: Max seconds to wait (default: 120)
        gpu: Optional GPU type (e.g. "T4", "A10G", "A100", "H100")
        environment: Optional Modal environment
    """
    # Build a small Python script that uses the Modal SDK to create a sandbox
    pip_install = ""
    if pip_packages:
        pkgs = ", ".join(f'"{p}"' for p in pip_packages)
        pip_install = f'.pip_install([{pkgs}])'

    gpu_arg = ""
    if gpu:
        gpu_arg = f', gpu="{gpu}"'

    env_arg = ""
    if environment:
        env_arg = f', environment_name="{environment}"'

    script = f'''
import modal
import sys

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
        f.flush()
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout + 60,  # extra buffer for image build
        )

        output = result.stdout
        # Parse the structured output
        parts = {}
        if "===STDOUT===" in output:
            parts["sandbox_stdout"] = output.split("===STDOUT===")[1].split("===STDERR===")[0].strip()
        if "===STDERR===" in output:
            parts["sandbox_stderr"] = output.split("===STDERR===")[1].split("===RC===")[0].strip()

        return json.dumps({
            "success": result.returncode == 0,
            "sandbox_output": parts.get("sandbox_stdout", ""),
            "sandbox_errors": parts.get("sandbox_stderr", ""),
            "runner_stderr": result.stderr.strip() if result.stderr else "",
        }, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "error": f"Sandbox timed out after {timeout + 60}s"})
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
    """Run Python code in a Modal sandbox. The code is executed as a script.

    Args:
        code: Python code to execute
        pip_packages: Optional list of pip packages to install
        python_version: Python version (default: "3.12")
        timeout: Max seconds (default: 120)
        gpu: Optional GPU type (e.g. "T4", "A10G", "A100", "H100")
    """
    # Write code to a temp file, then use run_sandbox_command
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir="/tmp") as f:
        f.write(code)
        f.flush()
        code_escaped = code.replace("'", "'\\''").replace('"', '\\"')

    # Use a heredoc-style approach to pass code safely
    command = f"python3 -c {json.dumps(code)}"

    return run_sandbox_command(
        command=command,
        pip_packages=pip_packages,
        python_version=python_version,
        timeout=timeout,
        gpu=gpu,
    )


# ---------------------------------------------------------------------------
# Secret tools
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

    result = _run_modal_cli(*args)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Environment & profile tools
# ---------------------------------------------------------------------------

@mcp.tool()
def current_profile() -> str:
    """Show the current Modal profile (workspace/user info)."""
    result = _run_modal_cli("profile", "current")
    return json.dumps(result, indent=2)


@mcp.tool()
def list_environments() -> str:
    """List all Modal environments in the current workspace."""
    result = _run_modal_cli("environment", "list")
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
