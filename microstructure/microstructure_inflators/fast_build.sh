#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

JOBS=$(nproc)
echo "=== Building microstructure_inflators using $JOBS CPU threads ==="

find . -name "CMakeLists.txt" -not -path "*/build*/*" -exec sed -i 's/cmake_minimum_required(VERSION 3\.[1-4])/cmake_minimum_required(VERSION 3.10)/g' {} + 2>/dev/null || true

mkdir -p build
cd build

cmake -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_POLICY_DEFAULT_CMP0074=NEW \
      -DCMAKE_POLICY_DEFAULT_CMP0135=NEW \
      -DMICRO_WITH_TBB=ON \
      ..

echo "=== Compiling cut_cells_cli, stitch_cells_cli, and stack_cells ==="
make -j$JOBS cut_cells_cli stitch_cells_cli stack_cells

echo "=== Build Complete! ==="
if [ -f "isosurface_inflator/cut_cells_cli" ]; then
    ls -lh isosurface_inflator/cut_cells_cli
fi
