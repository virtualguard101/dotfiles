#!/usr/bin/env bash

set -ueo pipefail

ARCH_DEV="/dev/nvme0n1p6"
MOUNT_POINT="/mnt/arch"

mkdir -p ${MOUNT_POINT}
sudo mount -o subvol=@ ${ARCH_DEV} ${MOUNT_POINT}
sudo mount -o subvol=@home ${ARCH_DEV} ${MOUNT_POINT}/home

echo "arch filesystem has been mounted at ${MOUNT_POINT}"
echo

ls ${MOUNT_POINT}
ls ${MOUNT_POINT}/home
