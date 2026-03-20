import subprocess
import threading
import time
import re
import os
import sys

def run_app():
    subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"], cwd=".")

def run_tunnel():
    print("\n--- Connecting to Cloudflare Tunnel (Please wait...) ---")
    # Using cloudflared to create a quick tunnel
    process = subprocess.Popen(
        ["./cloudflared.exe", "tunnel", "--url", "http://localhost:8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd="."
    )
    
    url_found = False
    for line in process.stdout:
        # Look for the trycloudflare URL in the output
        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
        if match:
            url = match.group(0)
            print("\n" + "="*50)
            print(f"  [PUBLIC ACCESS ENABLED]")
            print(f"  URL: {url}")
            print("="*50 + "\n")
            url_found = True
        
        # Also print the line if you want to see logs (optional)
        # if not url_found: print(line.strip())

if __name__ == "__main__":
    if not os.path.exists("cloudflared.exe"):
        print("Error: cloudflared.exe not found. Please download it first.")
        sys.exit(1)

    # Start the app in a separate thread
    app_thread = threading.Thread(target=run_app, daemon=True)
    app_thread.start()
    
    # Wait a bit for the app to start
    time.sleep(2)
    
    # Run the tunnel in the main thread (to keep it alive)
    run_tunnel()
