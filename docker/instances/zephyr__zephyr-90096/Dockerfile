FROM embedbench-zephyr-base:latest

WORKDIR /testbed

ARG BASE_COMMIT=c4ed1c01eb54d4026d1c040c852703856b152d6d
RUN git clone --filter=blob:none https://github.com/zephyrproject-rtos/zephyr.git . \
    && git checkout ${BASE_COMMIT}

RUN west init -l . \
    && west update --narrow -o=--depth=1 \
    && west zephyr-export

ARG ZEPHYR_SDK_VERSION=0.16.8
RUN ARCH=$(uname -m) \
    && SDK_ARCHIVE="zephyr-sdk-${ZEPHYR_SDK_VERSION}_linux-${ARCH}.tar.xz" \
    && wget -q "https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v${ZEPHYR_SDK_VERSION}/${SDK_ARCHIVE}" \
    && tar -xf "${SDK_ARCHIVE}" -C /opt \
    && rm "${SDK_ARCHIVE}" \
    && /opt/zephyr-sdk-${ZEPHYR_SDK_VERSION}/setup.sh -t x86_64-zephyr-elf -h -c

RUN west packages pip --install 2>/dev/null \
    || pip install -r scripts/requirements.txt

COPY test_patch.diff /tmp/
RUN git apply /tmp/test_patch.diff

ARG PLATFORM=qemu_x86
ARG TEST_PATH=tests/lib/c_lib/time
RUN west build -b ${PLATFORM} ${TEST_PATH} || true

RUN ctags -R --languages=C,C++ --exclude=build -f /testbed/tags .
