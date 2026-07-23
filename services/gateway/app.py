import platform
import time


def main() -> None:
    print("OMK gateway container started")
    print(f"Architecture: {platform.machine()}", flush=True)

    while True:
        print("OMK gateway is running", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
    