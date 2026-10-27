# AgentOS command sandbox image.
# Build:  docker build -f docker/sandbox.Dockerfile -t agentos-sandbox:latest .
# Every agent command runs in a fresh container from this image with no network,
# a read-only root filesystem, dropped capabilities and CPU/memory/PID limits
# (see agent/sandbox.py: DockerSandbox.container_kwargs).
FROM python:3.11-slim

RUN pip install --no-cache-dir "pytest>=8.2,<9" \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin sandbox

USER 1000:1000
WORKDIR /workspace
