"""Root shim for the live-pod graph entry point."""

from incident_agent.cli.live_pod import main

if __name__ == "__main__":
    raise SystemExit(main())
