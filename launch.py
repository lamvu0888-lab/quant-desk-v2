"""Single launcher. Uses a local venv, installs only when requirements change."""
from pathlib import Path
import hashlib
import os
import socket
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parent


def main():
    if sys.version_info<(3,11):
        print('Python 3.11 or newer is required. Python 3.12 is recommended.');return 2
    os.chdir(ROOT)
    diagnostic='--diagnose' in sys.argv
    if not diagnostic:
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',8501))==0:
                print('Port 8501 already has an app running. Open http://localhost:8501, or stop your old dashboard first.');return 1
    folder=ROOT/'.venv';python=folder/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        print('Creating the local Python environment…',flush=True);venv.create(folder,with_pip=True)
    signature=hashlib.sha256((ROOT/'requirements.txt').read_bytes()).hexdigest()
    stamp=folder/'.requirements-sha256'
    if not stamp.exists() or stamp.read_text()!=signature:
        print('Installing dashboard dependencies…',flush=True)
        result=subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements.txt')],cwd=ROOT)
        if result.returncode: return result.returncode
        stamp.write_text(signature)
    config=ROOT/'.env'
    if not config.exists():
        config.write_bytes((ROOT/'.env.example').read_bytes())
        print('Created .env. Add your Tradytics key there. Webull credentials are optional.',flush=True)
    if diagnostic:
        return subprocess.call([str(python),str(ROOT/'diagnose_webull.py')],cwd=ROOT)
    classic='--classic' in sys.argv
    script='dashboard.py' if classic else 'terminal.py'
    theme=['--theme.base','light','--theme.backgroundColor','#F7F9FB','--theme.secondaryBackgroundColor','#FFFFFF',
           '--theme.textColor','#172B3A'] if classic else []
    print('Quant Desk 2.5 · http://localhost:8501 · press Ctrl+C to stop.',flush=True)
    return subprocess.call([str(python),'-m','streamlit','run',str(ROOT/script),
        '--server.address','127.0.0.1','--server.port','8501','--browser.gatherUsageStats','false',*theme],cwd=ROOT)


if __name__=='__main__':
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(0)
