# The plugin's image. It runs beside a sidecar in the deployment's cluster, and
# reaches nothing but that sidecar and OANDA's v20 API.
#
# Built on the base image for the SDK this plugin pins: Python and
# open-meridian, the same version as pyproject.toml's, already installed. So
# the image's own layer is this plugin alone, and an upload sends only that.
# Move the two together when moving to a new SDK.
#
# BASE is for building against a base made locally rather than a released one.
# PLACEHOLDER (v25): plugin-python:0.29.0 is not published yet (it is, with open-meridian 0.29.0).
ARG BASE=ghcr.io/open-meridian/plugin-python:0.29.0
FROM ${BASE}
WORKDIR /plugin
# Installed from a mount rather than copied, so the image holds the installed
# package and no source beside it.
RUN --mount=type=bind,target=/tmp/src,rw pip install --no-cache-dir /tmp/src
USER 65532
CMD ["oanda"]
