#!/usr/bin/env bash
set -euo pipefail

# Package completed local baseline artifacts into one archive that can be
# uploaded as a private Kaggle Dataset.
#
# Usage:
#   bash scripts/package_kaggle_baseline.sh <kaggle-username>
#
# Output:
#   kaggle_upload/vifinqa-baseline-artifacts/
#       dataset-metadata.json
#       vifinqa_baseline_artifacts.tar.gz
#       sha256sums.txt

KAGGLE_USERNAME="${1:-}"
if [[ -z "${KAGGLE_USERNAME}" ]]; then
  echo "Usage: $0 <kaggle-username>" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS="${ROOT}/artifacts"
OUT="${ROOT}/kaggle_upload/vifinqa-baseline-artifacts"
ARCHIVE="${OUT}/vifinqa_baseline_artifacts.tar.gz"

required=(
  "table_assets.jsonl"
  "lexical_index.sqlite3"
  "dense.index"
  "dense_uids.jsonl"
)

for name in "${required[@]}"; do
  if [[ ! -f "${ARTIFACTS}/${name}" ]]; then
    echo "Missing required artifact: ${ARTIFACTS}/${name}" >&2
    exit 1
  fi
done

mkdir -p "${OUT}"
rm -f "${ARCHIVE}" "${OUT}/sha256sums.txt"

extra=()
[[ -f "${ARTIFACTS}/dense.index.meta.json" ]] && extra+=("dense.index.meta.json")
[[ -d "${ARTIFACTS}/question_router" ]] && extra+=("question_router")

files=("${required[@]}" "${extra[@]}")
archive_paths=()
for name in "${files[@]}"; do
  archive_paths+=("artifacts/${name}")
done

printf 'Packaging baseline artifacts:\n'
printf '  %s\n' "${archive_paths[@]}"

# Store paths relative to repository root so extraction recreates artifacts/.
tar -czf "${ARCHIVE}" -C "${ROOT}" "${archive_paths[@]}"

(
  cd "${OUT}"
  sha256sum "$(basename "${ARCHIVE}")" > sha256sums.txt
)

cat > "${OUT}/dataset-metadata.json" <<EOF
{
  "title": "ViFinQA Baseline Artifacts",
  "id": "${KAGGLE_USERNAME}/vifinqa-baseline-artifacts",
  "licenses": [
    {"name": "other"}
  ],
  "description": "Private reusable baseline artifacts for the nlp-finance-query ViFinQA project. Contains derived table assets and lexical/dense retrieval indexes; no model training labels are implied."
}
EOF

cat <<EOF

Ready for Kaggle upload:
  ${OUT}

Archive size:
$(du -h "${ARCHIVE}" | awk '{print "  " $1}')

Verify:
  cd "${OUT}" && sha256sum -c sha256sums.txt

Install/authenticate Kaggle CLI, then create a PRIVATE dataset:
  python -m pip install -U kaggle
  kaggle datasets create -p "${OUT}"

The Kaggle CLI creates datasets privately by default; do not add --public.
EOF
