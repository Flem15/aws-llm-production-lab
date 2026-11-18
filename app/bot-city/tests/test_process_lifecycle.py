import subprocess
import sys
import textwrap
import time


def terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def test_sigterm_handler_allows_clean_process_exit():
    code = textwrap.dedent(
        """
        import threading
        import time
        from run_bots import install_signal_handlers

        stop = threading.Event()
        install_signal_handlers(stop)
        print('READY', flush=True)
        while not stop.wait(0.05):
            pass
        print('STOPPED', flush=True)
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "READY"
        process.terminate()  # SIGTERM
        stdout, stderr = process.communicate(timeout=3)
        assert process.returncode == 0, stderr
        assert "STOPPED" in stdout
    finally:
        # Standing CI rule: SIGTERM, wait for exit, then SIGKILL fallback.
        terminate_process(process)
