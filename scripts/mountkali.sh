#!/usr/bin/env bash

set -ueo pipefail

KALI_DEV="/dev/nvme0n1p3"
MOUNT_POINT="/mnt/kali"

mkdir -p ${MOUNT_POINT}
sudo mount ${KALI_DEV} ${MOUNT_POINT}

echo "kali filesystem has been mounted at ${MOUNT_POINT}"
echo

ls ${MOUNT_POINT}
ls ${MOUNT_POINT}/home
