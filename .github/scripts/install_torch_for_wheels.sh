#!/usr/bin/env bash

set -euo pipefail

torch_version=${TORCH_VERSION:-2.4.1}
torch_index=${TORCH_INDEX:-cpu}

if [[ -z "${torch_version}" ]]; then
  echo "TORCH_VERSION is empty"
  exit 1
fi

if [[ -z "${torch_index}" ]]; then
  echo "TORCH_INDEX is empty"
  exit 1
fi

index_url="https://download.pytorch.org/whl/${torch_index}"

echo "Installing torch ${torch_version} from ${index_url}"
python -m pip install --upgrade pip
python -m pip install "torch==${torch_version}" --index-url "${index_url}"
python - <<'PY'
import torch
print('torch version:', torch.__version__)
print('cuda available:', torch.cuda.is_available())
PY
