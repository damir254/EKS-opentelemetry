#!/usr/bin/env bash

set -euo pipefail
full_path=$(realpath "$0")
dir_path=$(dirname "$full_path")
cd "$dir_path"/../../src/payment/
npm ci
npm test 
cd "../../"
echo "Unit tests: success!"
echo "Building docker image..."
docker build -f src/payment/Dockerfile -t payment-exercise:local . 
echo "Docker image build: success!"


