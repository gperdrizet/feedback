#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

ensure_cmd() {
  local cmd="$1"
  local pkg="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Installing ${pkg}..."
    ${SUDO} apt-get update
    ${SUDO} apt-get install -y "$pkg"
  fi
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1; then
    echo "Docker already installed"
    return
  fi

  echo "Installing Docker Engine..."
  ensure_cmd curl curl
  ensure_cmd gpg gnupg

  ${SUDO} install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | ${SUDO} gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  ${SUDO} chmod a+r /etc/apt/keyrings/docker.gpg

  . /etc/os-release
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" | \
    ${SUDO} tee /etc/apt/sources.list.d/docker.list >/dev/null

  ${SUDO} apt-get update
  ${SUDO} apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  ${SUDO} systemctl enable docker
  ${SUDO} systemctl start docker
}

ensure_compose() {
  if docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin already available"
    return
  fi

  echo "Installing Docker Compose plugin..."
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y docker-compose-plugin
}

main() {
  ensure_cmd git git
  ensure_docker
  ensure_compose

  ${SUDO} mkdir -p /opt/feedback
  ${SUDO} chown "${USER}:${USER}" /opt/feedback

  echo "Bootstrap complete. /opt/feedback is ready."
}

main "$@"
