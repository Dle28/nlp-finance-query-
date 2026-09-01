#!/usr/bin/env python3
"""Populate the scaffolded Kaggle experiment notebook reproducibly."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebooks/vifinqa_rag_finetune_kaggle_v1.ipynb"


def markdown(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text.splitlines(keepends=True),
    }


def main() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    notebook["cells"] = [
        markdown(
            """# ViFinQA RAG + Fine-tune trên Kaggle GPU

Mục tiêu: huấn luyện ba tầng độc lập từ dữ liệu tự giám sát sinh bởi bảng BCTC,
không dùng 1.012 câu test làm dữ liệu train.

Tiêu chí đạt:

- retriever fine-tuned phải tăng Recall@5 và MRR@5 trên các ticker giữ lại;
- reranker phải tăng tỷ lệ positive đứng trước hard negative;
- Qwen LoRA phải tăng JSON validity và AST exact match;
- chỉ đóng gói model nếu metric tốt hơn baseline.

Leaderboard trước thí nghiệm: r1 Answer 0,0870 / Tables F2 0,1601 / Docs F2 0,4714;
r2 Answer 0,1126 / Tables F2 0,2389 / Docs F2 0,6545.
"""
        ),
        markdown(
            """## 1. Thiết lập tái lập

Notebook mặc định chạy nhanh để kiểm tra (`FAST_DEV_RUN=True`). Chuyển sang
`False` sau khi toàn bộ cell chạy qua một lần. Bật GPU T4/P100 và Internet,
hoặc attach sẵn bốn model trong cấu hình.
"""
        ),
        code(
            """from pathlib import Path
import hashlib, json, os, random, shutil, subprocess, sys, time
import numpy as np
import torch

SEED = 20260828
random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
FAST_DEV_RUN = True
INPUT_ROOT = Path('/kaggle/input')
WORK_ROOT = Path('/kaggle/working/vifinqa_rag_finetune_v1')
WORK_ROOT.mkdir(parents=True, exist_ok=True)

MODELS = {
    'retriever': 'intfloat/multilingual-e5-small',
    'teacher_optional': 'BAAI/bge-m3',
    'reranker': 'BAAI/bge-reranker-v2-m3',
    'generator': 'Qwen/Qwen2.5-Coder-7B-Instruct',
}
MODEL_REVISIONS = {
    'retriever': '614241f622f53c4eeff9890bdc4f31cfecc418b3',
    'teacher_optional': '5617a9f61b028005a4858fdac845db406aefb181',
    'reranker': '953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e',
    'generator': 'c03e6d358207e414f1eca0bb1891e29f1db0e242',
}

def sha256_path(path):
    path = Path(path)
    digest = hashlib.sha256()
    if path.is_file():
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
    if not path.is_dir():
        raise FileNotFoundError(path)
    for child in sorted(path.rglob('*')):
        if child.is_file():
            digest.update(child.relative_to(path).as_posix().encode('utf-8'))
            digest.update(str(child.stat().st_size).encode('ascii'))
            with child.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
    return digest.hexdigest()

print({'cuda': torch.cuda.is_available(), 'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None})
assert torch.cuda.is_available(), 'Hãy bật GPU cho notebook Kaggle.'
"""
        ),
        markdown("## 2. Tìm curriculum đã attach và kiểm tra không rò rỉ test"),
        code(
            """def scan_curriculum(root: Path):
    for manifest_path in root.rglob('manifest.json'):
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            continue
        if manifest.get('protocol') == 'vifinqa_self_supervised_curriculum_v1':
            return manifest_path.parent
    return None

def find_curriculum(root: Path) -> Path:
    found=scan_curriculum(root)
    if found: return found
    package=next(root.rglob('vifinqa_rag_finetune_kaggle_v1.zip'), None)
    if package:
        extracted=WORK_ROOT/'attached_package'
        shutil.unpack_archive(str(package), extracted)
        found=scan_curriculum(extracted)
        if found: return found
    raise FileNotFoundError('Không tìm thấy curriculum. Hãy attach ZIP/dataset đã đóng gói.')

CURRICULUM = find_curriculum(INPUT_ROOT)
manifest = json.loads((CURRICULUM / 'manifest.json').read_text())
assert manifest['competition_questions_read'] == 0
assert not any(manifest['split_ticker_overlap'].values())
print(json.dumps({k: manifest[k] for k in ('counts','eligible_table_count','split_ticker_overlap')}, indent=2, ensure_ascii=False))
"""
        ),
        markdown("## 3. Cài thư viện huấn luyện"),
        code(
            """packages = [
    'sentence-transformers>=5,<6', 'datasets>=3,<5', 'transformers>=4.51,<5',
    'accelerate>=1.6,<2', 'peft>=0.15,<0.19', 'trl>=0.18,<0.25',
    'bitsandbytes>=0.45,<0.49'
]
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *packages], check=True)
"""
        ),
        markdown("## 4. Đọc dữ liệu và giới hạn smoke run"),
        code(
            """from datasets import Dataset

def load_jsonl(name, limit=None):
    rows=[]
    with (CURRICULUM / name).open() as f:
        for line in f:
            rows.append(json.loads(line))
            if limit and len(rows) >= limit: break
    return rows

limits = {'train': 4000, 'validation': 1000, 'test': 1000} if FAST_DEV_RUN else {'train': None, 'validation': None, 'test': None}
retrieval = {s: load_jsonl(f'retrieval_triplets_{s}.jsonl', limits[s]) for s in limits}
reranker = {s: load_jsonl(f'reranker_pairs_{s}.jsonl', limits[s] * 2 if limits[s] else None) for s in limits}
program = {s: load_jsonl(f'program_sft_{s}.jsonl', limits[s]) for s in limits}
print({k: {s: len(v[s]) for s in v} for k,v in {'retrieval':retrieval,'reranker':reranker,'program':program}.items()})
"""
        ),
        markdown(
            """## 5. Baseline retriever

Đánh giá trên pool held-out gồm positive và hard negative. Đây là phép đo
offline theo ticker chưa xuất hiện trong train, không phải điểm leaderboard.
"""
        ),
        code(
            """from sentence_transformers import SentenceTransformer, util

def evaluate_retriever(model, rows, top_k=10):
    passages={}
    for row in rows:
        passages[row['positive_uid']] = row['positive']
        passages[row['negative_uid']] = row['negative']
    uids=list(passages); corpus=[passages[u] for u in uids]; uid_to_idx={u:i for i,u in enumerate(uids)}
    queries=[r['anchor'] for r in rows]
    q_emb=model.encode(queries, batch_size=128, normalize_embeddings=True, convert_to_tensor=True, show_progress_bar=True)
    c_emb=model.encode(corpus, batch_size=128, normalize_embeddings=True, convert_to_tensor=True, show_progress_bar=True)
    hits=util.semantic_search(q_emb, c_emb, top_k=min(top_k,len(corpus)))
    ranks=[]
    for row, result in zip(rows, hits):
        target=uid_to_idx[row['positive_uid']]
        rank=next((i+1 for i,h in enumerate(result) if h['corpus_id']==target), None)
        ranks.append(rank)
    return {
        'recall_at_1': sum(r==1 for r in ranks)/len(ranks),
        'recall_at_5': sum(r is not None and r<=5 for r in ranks)/len(ranks),
        'recall_at_10': sum(r is not None and r<=10 for r in ranks)/len(ranks),
        'mrr_at_5': sum(1/r for r in ranks if r is not None and r<=5)/len(ranks),
    }

retriever = SentenceTransformer(MODELS['retriever'], revision=MODEL_REVISIONS['retriever'], device='cuda')
baseline_retrieval = evaluate_retriever(retriever, retrieval['test'])
baseline_retrieval
"""
        ),
        markdown("## 6. Fine-tune retriever với in-batch + hard negatives"),
        code(
            """from sentence_transformers import SentenceTransformerTrainer, SentenceTransformerTrainingArguments
from sentence_transformers.losses import MultipleNegativesRankingLoss
from sentence_transformers.training_args import BatchSamplers

train_ds=Dataset.from_list([{k:r[k] for k in ('anchor','positive','negative')} for r in retrieval['train']])
retriever_args=SentenceTransformerTrainingArguments(
    output_dir=str(WORK_ROOT/'retriever_checkpoints'), num_train_epochs=1,
    per_device_train_batch_size=32 if FAST_DEV_RUN else 64,
    learning_rate=2e-5, warmup_ratio=0.05, fp16=True,
    batch_sampler=BatchSamplers.NO_DUPLICATES, save_strategy='epoch', logging_steps=50,
    report_to='none', seed=SEED,
)
retriever_trainer=SentenceTransformerTrainer(
    model=retriever, args=retriever_args, train_dataset=train_ds,
    loss=MultipleNegativesRankingLoss(retriever),
)
retriever_trainer.train()
retriever_dir=WORK_ROOT/'retriever_finetuned'
retriever.save_pretrained(retriever_dir)
finetuned_retrieval=evaluate_retriever(retriever, retrieval['test'])
{'baseline':baseline_retrieval,'finetuned':finetuned_retrieval}
"""
        ),
        markdown("## 7. Fine-tune reranker trên positive/hard-negative pairs"),
        code(
            """from sentence_transformers.cross_encoder import CrossEncoder, CrossEncoderTrainer, CrossEncoderTrainingArguments
from sentence_transformers.cross_encoder.losses import BinaryCrossEntropyLoss

reranker_model=CrossEncoder(MODELS['reranker'], revision=MODEL_REVISIONS['reranker'], num_labels=1, max_length=512, device='cuda')
reranker_train=Dataset.from_list(reranker['train']).select_columns(['query','passage','label'])
reranker_args=CrossEncoderTrainingArguments(
    output_dir=str(WORK_ROOT/'reranker_checkpoints'), num_train_epochs=1,
    per_device_train_batch_size=8 if FAST_DEV_RUN else 16,
    learning_rate=1e-5, fp16=True, save_strategy='epoch', logging_steps=50,
    report_to='none', seed=SEED,
)
def reranker_rank_metrics(model, rows):
    grouped={}
    for row in rows:
        grouped.setdefault(row['id'].rsplit(':',1)[0], []).append(row)
    ranks=[]
    for candidates in grouped.values():
        scores=model.predict([[row['query'],row['passage']] for row in candidates])
        ranked=sorted(zip(candidates, scores), key=lambda item: float(item[1]), reverse=True)
        positive_rank=next((rank for rank, (row, _) in enumerate(ranked, start=1) if int(row['label']) == 1), None)
        if positive_rank is not None:
            ranks.append(positive_rank)
    return {
        'recall_at_5': sum(rank <= 5 for rank in ranks)/max(1,len(ranks)),
        'mrr_at_5': sum(1/rank for rank in ranks if rank <= 5)/max(1,len(ranks)),
        'count': len(ranks),
    }

def pair_accuracy(model, rows):
    grouped={}
    for row in rows:
        grouped.setdefault(row['id'].rsplit(':',1)[0], {})[int(row['label'])]=row
    good=total=0
    for pair in grouped.values():
        if 0 not in pair or 1 not in pair: continue
        scores=model.predict([[pair[1]['query'],pair[1]['passage']],[pair[0]['query'],pair[0]['passage']]])
        good += float(scores[0]) > float(scores[1]); total += 1
    return good/max(1,total)

baseline_reranker_metrics=reranker_rank_metrics(reranker_model, reranker['test'])
baseline_reranker_pair_accuracy=pair_accuracy(reranker_model, reranker['test'])
reranker_trainer=CrossEncoderTrainer(
    model=reranker_model, args=reranker_args, train_dataset=reranker_train,
    loss=BinaryCrossEntropyLoss(reranker_model),
)
reranker_trainer.train()
reranker_dir=WORK_ROOT/'reranker_finetuned'
reranker_model.save_pretrained(reranker_dir)
finetuned_reranker_metrics=reranker_rank_metrics(reranker_model, reranker['test'])
finetuned_reranker_pair_accuracy=pair_accuracy(reranker_model, reranker['test'])
{
    'baseline': baseline_reranker_metrics,
    'finetuned': finetuned_reranker_metrics,
    'baseline_pair_accuracy': baseline_reranker_pair_accuracy,
    'finetuned_pair_accuracy': finetuned_reranker_pair_accuracy,
}
"""
        ),
        markdown(
            """## 8. QLoRA cho bộ sinh AST

Mô hình chỉ sinh JSON AST. Giá trị số vẫn phải lấy từ CSV nguồn và executor
deterministic thực thi; model không được ghi trực tiếp `answer`.
"""
        ),
        code(
            """from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from peft import LoraConfig
from trl import SFTConfig, SFTTrainer

generator_id=MODELS['generator']
tokenizer=AutoTokenizer.from_pretrained(generator_id, revision=MODEL_REVISIONS['generator'], trust_remote_code=True)
bnb=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
generator=AutoModelForCausalLM.from_pretrained(generator_id, revision=MODEL_REVISIONS['generator'], quantization_config=bnb, device_map='auto', trust_remote_code=True)
generator.config.use_cache=False

def ast_eval(model, tokenizer, rows, limit=100):
    valid=exact=0; total=min(limit,len(rows)); model.eval()
    for row in rows[:total]:
        prompt=tokenizer.apply_chat_template(row['messages'][:-1], tokenize=False, add_generation_prompt=True)
        inputs=tokenizer(prompt, return_tensors='pt').to(model.device)
        with torch.no_grad(): out=model.generate(**inputs, max_new_tokens=220, do_sample=False)
        text=tokenizer.decode(out[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True).strip()
        try:
            decoder=json.JSONDecoder()
            predicted, end=decoder.raw_decode(text[text.find('{'):])
            if not isinstance(predicted, dict) or text[text.find('{')+end:].strip(): raise ValueError('non-canonical JSON')
            valid += 1
            expected=json.loads(row['messages'][-1]['content'])
            exact += predicted == expected
        except Exception: pass
    return {'json_validity':valid/max(1,total),'ast_exact_match':exact/max(1,total),'count':total}

baseline_program_metrics=ast_eval(generator, tokenizer, program['test'], limit=50 if FAST_DEV_RUN else 500)

def render(row):
    return {'text': tokenizer.apply_chat_template(row['messages'], tokenize=False, add_generation_prompt=False)}

sft_train=Dataset.from_list(program['train']).map(render, remove_columns=['id','messages'])
lora=LoraConfig(
    r=16, lora_alpha=32, lora_dropout=0.05, bias='none', task_type='CAUSAL_LM',
    target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'],
)
sft_args=SFTConfig(
    output_dir=str(WORK_ROOT/'generator_checkpoints'), num_train_epochs=1,
    per_device_train_batch_size=1, gradient_accumulation_steps=8 if FAST_DEV_RUN else 16,
    learning_rate=2e-4, warmup_ratio=0.03, fp16=True, logging_steps=20,
    save_strategy='epoch', report_to='none', max_length=1536, dataset_text_field='text', seed=SEED,
)
sft_trainer=SFTTrainer(model=generator, args=sft_args, train_dataset=sft_train, peft_config=lora, processing_class=tokenizer)
sft_trainer.train()
generator_dir=WORK_ROOT/'generator_lora'
sft_trainer.model.save_pretrained(generator_dir); tokenizer.save_pretrained(generator_dir)
finetuned_program_metrics=ast_eval(sft_trainer.model, tokenizer, program['test'], limit=50 if FAST_DEV_RUN else 500)
"""
        ),
        markdown("## 9. Đánh giá JSON validity và AST exact match"),
        code(
            """{
    'baseline': baseline_program_metrics,
    'finetuned': finetuned_program_metrics,
}
"""
        ),
        markdown("## 10. Promotion gate và đóng gói"),
        code(
            """metrics={
    'baseline_retrieval':baseline_retrieval,
    'finetuned_retrieval':finetuned_retrieval,
    'baseline_reranker':baseline_reranker_metrics,
    'finetuned_reranker':finetuned_reranker_metrics,
    'baseline_reranker_pair_accuracy':baseline_reranker_pair_accuracy,
    'finetuned_reranker_pair_accuracy':finetuned_reranker_pair_accuracy,
    'baseline_program':baseline_program_metrics,
    'finetuned_program':finetuned_program_metrics,
    'fast_dev_run':FAST_DEV_RUN,
    'seed':SEED,
    'evaluation_protocol': {
        'full_run': not FAST_DEV_RUN,
        'held_out_split': 'test',
        'competition_questions_used': 0,
        'promotion_requires_full_run': True,
    },
}

def non_decreasing(baseline, candidate, keys):
    return all(candidate[key] >= baseline[key] for key in keys)

retriever_promoted=(
    non_decreasing(baseline_retrieval, finetuned_retrieval, ['recall_at_5','mrr_at_5'])
    and not FAST_DEV_RUN
)
reranker_promoted=(
    non_decreasing(baseline_reranker_metrics, finetuned_reranker_metrics, ['recall_at_5','mrr_at_5'])
    and not FAST_DEV_RUN
)
generator_promoted=(
    non_decreasing(baseline_program_metrics, finetuned_program_metrics, ['json_validity','ast_exact_match'])
    and not FAST_DEV_RUN
)

common_hashes={
    'code': hashlib.sha256(json.dumps({'seed':SEED,'models':MODELS,'revisions':MODEL_REVISIONS}, sort_keys=True).encode()).hexdigest(),
    'index': sha256_path(CURRICULUM / 'retrieval_triplets_test.jsonl'),
    'prompt': sha256_path(CURRICULUM / 'program_sft_train.jsonl'),
    'input': sha256_path(CURRICULUM),
}
metrics['held_out_metrics']={
    'retriever': {
        'model_kind':'retriever', 'full_run':not FAST_DEV_RUN,
        'baseline':baseline_retrieval, 'candidate':finetuned_retrieval,
        'hashes':{**common_hashes, 'model':sha256_path(retriever_dir)},
    },
    'reranker': {
        'model_kind':'reranker', 'full_run':not FAST_DEV_RUN,
        'baseline':baseline_reranker_metrics, 'candidate':finetuned_reranker_metrics,
        'hashes':{**common_hashes, 'model':sha256_path(reranker_dir)},
    },
}
metrics['promotion_decisions']={
    'retriever': {'decision':'APPROVE' if retriever_promoted else 'INELIGIBLE', 'promotion_allowed':retriever_promoted},
    'reranker': {'decision':'APPROVE' if reranker_promoted else 'INELIGIBLE', 'promotion_allowed':reranker_promoted},
    'generator': {'decision':'APPROVE' if generator_promoted else 'INELIGIBLE', 'promotion_allowed':generator_promoted},
}
metrics['retriever_promotion_allowed']=retriever_promoted
metrics['reranker_promotion_allowed']=reranker_promoted
metrics['generator_promotion_allowed']=generator_promoted
metrics['all_model_components_promotion_allowed']=retriever_promoted and reranker_promoted and generator_promoted
metrics['artifact_role']='candidate_until_explicit_promotion'
(WORK_ROOT/'metrics.json').write_text(json.dumps(metrics,indent=2,ensure_ascii=False))

archive=shutil.make_archive('/kaggle/working/vifinqa_rag_finetuned_v1','zip',WORK_ROOT)
print({'archive':archive,'metrics':metrics})
"""
        ),
        markdown(
            """## 11. Sau khi chạy

Tải `/kaggle/working/vifinqa_rag_finetuned_v1.zip`. Chỉ dùng model khi
`FAST_DEV_RUN=False`; từng component phải vượt hoặc giữ cả hai metric held-out
của chính nó, đủ hash artifact, rồi mới có `*_promotion_allowed=true`. Đây chỉ
là cổng cho model navigation/AST candidate; vẫn phải chạy submission ablation
và quyết định release riêng. Nếu không đạt, giữ baseline; không tự động thay
model production.
"""
        ),
    ]
    NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(NOTEBOOK)


if __name__ == "__main__":
    main()
