FROM ubuntu:20.04

ARG DEBIAN_FRONTEND=noninteractive
ARG TZ=Etc/UTC
ARG HOST_UID=1000
ARG HOST_GID=1000
ARG http_proxy
ARG https_proxy
ARG no_proxy

ENV TZ=${TZ}
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV CCACHE_DIR=/ccache
ENV PATH=/usr/lib/ccache:${PATH}
ENV http_proxy=${http_proxy}
ENV https_proxy=${https_proxy}
ENV no_proxy=${no_proxy}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    ccache \
    file \
    g++-aarch64-linux-gnu \
    gcc-aarch64-linux-gnu \
    gdb \
    git \
    libboost-all-dev \
    libelf-dev \
    libgoogle-perftools-dev \
    libhdf5-serial-dev \
    libpng-dev \
    libprotobuf-dev \
    libprotoc-dev \
    libc6-dev-arm64-cross \
    m4 \
    pkg-config \
    protobuf-compiler \
    python3 \
    python3-dev \
    python3-pip \
    scons \
    swig \
    xz-utils \
    zlib1g-dev

RUN apt-get install -y --no-install-recommends valgrind

RUN groupadd --gid ${HOST_GID} builder && \
    useradd --uid ${HOST_UID} --gid ${HOST_GID} --create-home --shell /bin/bash builder && \
    mkdir -p /workspace /ccache && \
    chown -R builder:builder /workspace /ccache /home/builder

COPY mold-2.34.0-x86_64-linux/bin/mold /usr/local/bin/mold
COPY mold-2.34.0-x86_64-linux/lib/mold/mold-wrapper.so /usr/local/lib/mold-wrapper.so
COPY ld /usr/bin/ld

USER builder
WORKDIR /workspace

CMD ["bash"]
