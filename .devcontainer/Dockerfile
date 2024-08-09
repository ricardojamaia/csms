# Use the official Home Assistant container as a base
FROM ghcr.io/home-assistant/devcontainer:stable

# Install additional dependencies if needed
RUN pip install pytest pytest-asyncio

# Set the environment variables for Home Assistant development
ENV PYTHONPATH=/usr/src/hass_config

# Optionally, install VSCode extensions for development
RUN code-server --install-extension ms-python.python \
                --install-extension esbenp.prettier-vscode \
                --install-extension VisualStudioExptTeam.vscodeintellicode \
                --install-extension ms-azuretools.vscode-docker

# Set the entrypoint to run Home Assistant in debug mode
ENTRYPOINT ["hass", "--debug", "--script", "check_config", "--config", "/config"]
