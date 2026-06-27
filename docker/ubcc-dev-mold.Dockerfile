FROM ubcc-dev:ubuntu20.04
COPY mold-2.34.0-x86_64-linux/bin/mold /usr/local/bin/mold
COPY mold-2.34.0-x86_64-linux/lib/mold/mold-wrapper.so /usr/local/lib/mold-wrapper.so
COPY ld /usr/bin/ld
