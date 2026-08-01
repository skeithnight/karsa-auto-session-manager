import subprocess
import sys
import time


def main():
    print("Background monitoring started.")
    try:
        with open("karsa_post_relaunch_monitor.log", "w") as f:
            p = subprocess.Popen(
                ["docker", "compose", "-f", "docker-compose.apps.yml", "logs", "-f", "karsa-live", "karsa-shadow", "karsa-backtest", "karsa-data-engine"],
                stdout=f,
                stderr=subprocess.STDOUT
            )
            print(f"Monitoring process started with PID {p.pid}")
            sys.stdout.flush()

            # Wait for 1 hour (3600 seconds)
            time.sleep(3600)

            print("Monitoring time completed. Terminating process...")
            p.terminate()
            p.wait()
            print("Background monitoring finished successfully.")
    except Exception as e:
        print(f"Monitoring error: {e}")

if __name__ == "__main__":
    main()
