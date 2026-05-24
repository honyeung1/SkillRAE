#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_DIR = REPO_ROOT / "experiments" / "retrieval_tasks_backend"
TEMPLATE_DOCX = REPO_ROOT / "reward_report.docx"
DEFAULT_OUTPUT = EXPERIMENT_DIR / "affiliated_rescue_algorithm_report.docx"
PACK_SCRIPT = REPO_ROOT / "global_skill_pool" / "docx" / "ooxml" / "scripts" / "pack.py"


def part(text: str, *, bold: bool = False, mono: bool = False, italic: bool = False) -> dict[str, object]:
    return {"text": text, "bold": bold, "mono": mono, "italic": italic}


def render_run(item: str | dict[str, object]) -> str:
    if isinstance(item, str):
        item = {"text": item}
    text = escape(str(item.get("text", "")))
    if not text:
        return ""
    rpr: list[str] = []
    if item.get("bold"):
        rpr.extend(["<w:b/>", "<w:bCs/>"])
    if item.get("italic"):
        rpr.extend(["<w:i/>", "<w:iCs/>"])
    if item.get("mono"):
        rpr.append('<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>')
    rpr_xml = f"<w:rPr>{''.join(rpr)}</w:rPr>" if rpr else ""
    return f"<w:r>{rpr_xml}<w:t xml:space=\"preserve\">{text}</w:t></w:r>"


def render_paragraph(
    items: str | list[str | dict[str, object]],
    *,
    style: str | None = None,
    center: bool = False,
    bullet: bool = False,
) -> str:
    parts = items if isinstance(items, list) else [items]
    ppr: list[str] = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    if bullet:
        ppr.append('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
    if center:
        ppr.append('<w:jc w:val="center"/>')
    ppr_xml = f"<w:pPr>{''.join(ppr)}</w:pPr>" if ppr else ""
    runs_xml = "".join(render_run(item) for item in parts)
    return f"<w:p>{ppr_xml}{runs_xml}</w:p>"


def render_table(
    rows: list[list[list[str | dict[str, object]] | str]],
    widths: list[int],
    *,
    header_rows: int = 0,
    center: bool = False,
) -> str:
    grid_xml = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    row_xml: list[str] = []
    for row_index, row in enumerate(rows):
        cell_xml: list[str] = []
        for cell_index, cell in enumerate(row):
            width = widths[cell_index]
            cell_parts = cell if isinstance(cell, list) else [cell]
            cell_center = center or (row_index == 0 and header_rows > 0)
            paras = [render_paragraph(cell_parts, center=cell_center)]
            tc_pr: list[str] = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
            if row_index < header_rows:
                tc_pr.append('<w:shd w:val="clear" w:color="auto" w:fill="D9EAF7"/>')
            cell_xml.append(f"<w:tc><w:tcPr>{''.join(tc_pr)}</w:tcPr>{''.join(paras)}</w:tc>")
        row_xml.append(f"<w:tr>{''.join(cell_xml)}</w:tr>")
    return (
        "<w:tbl>"
        "<w:tblPr>"
        '<w:tblStyle w:val="TableGrid"/>'
        '<w:tblW w:w="0" w:type="auto"/>'
        "</w:tblPr>"
        f"<w:tblGrid>{grid_xml}</w:tblGrid>"
        f"{''.join(row_xml)}"
        "</w:tbl>"
    )


def render_flow_table(steps: list[str]) -> str:
    rows: list[list[str]] = []
    for index, step in enumerate(steps):
        rows.append([step])
        if index != len(steps) - 1:
            rows.append(["↓"])
    return render_table(rows, [9000], center=True)


def build_blocks() -> list[str]:
    flow_steps = [
        "输入任务集：`TASKS_FILE` 逐个读取任务目录与 instruction.md",
        "Shell runner 透传 `RETRIEVAL_MODE=topk_context_selected_affiliated_rescue`、`TOP_K`、`SYNTHESIZED_SKILL_POSITION_MODE` 到 `prepare_retrieval_task_mirrors.py`",
        "分层检索产出 `selected_skill_records` 与 `rescue_candidate_skill_records`，并形成 retrieval metadata",
        "物化 task mirror：复制 selected skills，生成 `affiliation_context_skill`，再向 selected skill 挂载 `.affiliation/AFFILIATED_CUES.md`",
        "Harbor 对 mirror 任务执行 `uv run harbor run -p <mirror_path>`，在 task-local skills 环境下完成 agent 运行",
        "Runner 汇总 `summary.jsonl`、`summary.txt`、`rows/*.json`、`rows_txt/*.txt`，同时保留 mirror manifest 与 `affiliation_manifest.json`",
    ]
    compare_rows: list[list[list[str | dict[str, object]] | str]] = [
        [
            [part("比较维度", bold=True)],
            [part("topk_context_selected_plus_rescue", bold=True, mono=True)],
            [part("topk_context_selected_affiliated_rescue", bold=True, mono=True)],
        ],
        [
            "主执行面",
            "selected skills + 一个 context skill；rescued subunits 直接写进 context skill 文本。",
            "selected skills 仍是主执行面；额外生成 affiliation coordinator 只负责路由，不接管事实来源。",
        ],
        [
            "rescue 内容落点",
            "作为“rescued high-value subunits”并入生成的 context skill。",
            "先做 affiliation 打分，再把 rescue 内容局部挂到 selected skill 下的 `.affiliation/AFFILIATED_CUES.md`。",
        ],
        [
            "生成物类型",
            [part("context_skill", mono=True)],
            [part("affiliation_context_skill", mono=True)],
        ],
        [
            "任务级 manifest",
            "主要记录 rescued subunits 与 selected skill 摘要。",
            [part("affiliation_manifest.json", mono=True), " 额外记录 active L2、attached/dropped rescued subunits、affiliated cue files。"],
        ],
        [
            "运行意图",
            "把非选中技能的高价值子单元直接塞进一个统一说明文档。",
            "保留 selected skills 为主界面，只把 rescue 信息作为 skill-local 补充线索，不扩展全局选中技能集合。",
        ],
    ]

    blocks: list[str] = [
        render_paragraph("SkillsBench `topk_context_selected_affiliated_rescue` 模式算法与执行流程汇报", style="Title"),
        render_paragraph(
            [
                "本文面向技术同事，解释 ",
                part("run_retrieval_tasks_backend.sh", mono=True),
                " 在 ",
                part("topk_context_selected_affiliated_rescue", mono=True),
                " 模式下的端到端执行链路、分层检索算法、rescue 子单元选择、affiliation 归属策略，以及最终产物如何落盘并交给 Harbor 执行。",
            ]
        ),
        render_paragraph(
            [
                "主线代码为 ",
                part("experiments/retrieval_tasks_backend/run_retrieval_tasks_backend.sh", mono=True),
                "、",
                part("experiments/retrieval_tasks_backend/prepare_retrieval_task_mirrors.py", mono=True),
                "、",
                part("retrieval.py", mono=True),
                "；辅助证据来自 ",
                part("rescue_selector.py", mono=True),
                "、",
                part("affiliation_exposure.py", mono=True),
                "、",
                part("context_skill_compiler.py", mono=True),
                " 和 ",
                part("tests/test_llm_topk_smoke.py", mono=True),
                "。",
            ]
        ),
        render_paragraph("1. 模式定义与执行入口", style="Heading1"),
        render_paragraph(
            [
                "这个模式不是 shell 内部单独写死的一套算法，而是由 runner 把环境变量 ",
                part("RETRIEVAL_MODE", mono=True),
                " 透传给 ",
                part("prepare_retrieval_task_mirrors.py", mono=True),
                "。换句话说，",
                part("run_retrieval_tasks_backend.sh", mono=True),
                " 只负责准备执行环境、日志和 Harbor 调度；真正的 affiliated rescue 逻辑落在 Python 侧的 mirror materialization 阶段。",
            ]
        ),
        render_paragraph(
            [
                "Runner 先记录并透传这些核心参数：",
                part("TASKS_FILE", mono=True),
                "、",
                part("MIRROR_ROOT", mono=True),
                "、",
                part("TOP_K", mono=True),
                "、",
                part("RETRIEVAL_MODE", mono=True),
                "、",
                part("SYNTHESIZED_SKILL_POSITION_MODE", mono=True),
                "，以及可选的 ",
                part("post_retrieval_rerank_*", mono=True),
                " 开关。",
            ]
        ),
        render_paragraph(
            part(
                "PYTHONPATH=<repo-root> $CONDA_PREFIX/bin/python experiments/retrieval_tasks_backend/prepare_retrieval_task_mirrors.py --tasks-file \"$TASKS_FILE\" --mirror-root \"$MIRROR_ROOT\" --manifest-out \"$MIRROR_MANIFEST\" --k \"$TOP_K\" --retrieval-mode \"$RETRIEVAL_MODE\" --synthesized-skill-position-mode \"$SYNTHESIZED_SKILL_POSITION_MODE\"",
                mono=True,
            )
        ),
        render_paragraph(
            [
                "这里的 ",
                part("topk_context_selected_affiliated_rescue", mono=True),
                " 已经被 ",
                part("prepare_retrieval_task_mirrors.py", mono=True),
                " 注册为合法 mode。外层 runner 本身不对 mode 语义做分支判断，它只负责把 mode 原样带给下游。",
            ]
        ),
        render_paragraph("2. 端到端执行链路", style="Heading1"),
        render_paragraph(
            "从输入任务文件到最终 Harbor job，这个模式可以拆成“任务读取 -> 检索 -> rescue 筛选 -> affiliation 挂载 -> mirror 执行 -> 汇总落盘”六段流水线。"
        ),
        render_paragraph("端到端流程图", center=True),
        render_flow_table(flow_steps),
        render_paragraph("分步解释如下：", center=False),
        render_paragraph(
            [
                part("1) ", bold=True),
                "runner 读取 ",
                part("TASKS_FILE", mono=True),
                "，并为每个任务找到 ",
                part("instruction.md", mono=True),
                " 作为检索查询文本。",
            ]
        ),
        render_paragraph(
            [
                part("2) ", bold=True),
                "若缓存命中，则直接复用缓存中的 ",
                part("retrieved_skills_ranked", mono=True),
                " 与 ",
                part("retrieval_metadata", mono=True),
                "；否则调用 retriever 在线检索。",
            ]
        ),
        render_paragraph(
            [
                part("3) ", bold=True),
                "mirror builder 根据 mode 决定如何把 selected skills 注入任务环境。对于 affiliated rescue，它会先复制 selected skills，再生成一个 ",
                part("affiliation_context_skill", mono=True),
                "，同时向部分 selected skills 旁边写入 ",
                part(".affiliation/AFFILIATED_CUES.md", mono=True),
                "。",
            ]
        ),
        render_paragraph(
            [
                part("4) ", bold=True),
                "随后 runner 用 ",
                part("uv run harbor run -p <mirror_path>", mono=True),
                " 对物化后的 mirror 执行 agent 任务。",
            ]
        ),
        render_paragraph(
            [
                part("5) ", bold=True),
                "每个任务执行完成后，runner 从 Harbor job 目录中提取 trial 结果、token、phase duration、reward 等指标，写入 ",
                part("rows/*.json", mono=True),
                " 和 ",
                part("rows_txt/*.txt", mono=True),
                "。",
            ]
        ),
        render_paragraph(
            [
                part("6) ", bold=True),
                "最终输出包括 ",
                part("mirror_manifest.json", mono=True),
                "、",
                part("summary.jsonl", mono=True),
                "、",
                part("summary.txt", mono=True),
                " 以及任务级别的 Harbor job artifacts。",
            ]
        ),
        render_paragraph("3. 检索算法", style="Heading1"),
        render_paragraph(
            [
                part("retrieval.py", mono=True),
                " 中的 ",
                part("SkillRetriever.retrieve_with_metadata(...)", mono=True),
                " 是 affiliated rescue 的上游召回器。这个 mode 不会自带另一套 retriever，而是复用同一份分层检索结果，再在后续阶段执行 rescue 与 affiliation。",
            ]
        ),
        render_paragraph(
            "检索分成四个信号通道，并在技能层做融合排序："
        ),
        render_paragraph(
            [
                part("1) L2 soft boost", bold=True),
                "：先把任务向量与 capability cluster 向量做相似度，选出 ",
                part("TOP_N_L2_CLUSTERS=2", mono=True),
                " 个 L2 cluster，把其中的技能加入 boost 集合。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("2) L1 representation matching", bold=True),
                "：把任务向量和每个技能的 canonical skill representation 做相似度，再做 min-max 归一化，得到 ",
                part("l1_norm", mono=True),
                "。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("3) Name prior", bold=True),
                "：把任务向量和 skill name 文本做相似度，形成语义先验 ",
                part("prior_norm", mono=True),
                "。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("4) L0 subunit graph projection", bold=True),
                "：从所有 subunit embedding 中选 ",
                part("TOP_N_SUBUNITS=30", mono=True),
                " 个最相似 subunits；对子单元连接的技能按 ",
                part("sim / degree", mono=True),
                " 做贡献累计，并且跳过 ",
                part("degree > HUB_THRESHOLD", mono=True),
                " 的 hub subunit。",
            ],
            bullet=True,
        ),
        render_paragraph(
            part("base_score = 0.6 * L1 + 0.4 * L0 + 0.15 * Prior", mono=True)
        ),
        render_paragraph(
            part("if skill in boosted_L2: final_score = base_score * 1.10", mono=True)
        ),
        render_paragraph(
            [
                "按 ",
                part("final_score", mono=True),
                " 排序后，前 ",
                part("k", mono=True),
                " 个技能进入 ",
                part("selected_skill_records", mono=True),
                "。每个技能最多保留 ",
                part("TOP_SELECTED_SUBUNITS_PER_SKILL=3", mono=True),
                " 个去重后的 ",
                part("top_subunits", mono=True),
                "，这些子单元随后会被 downstream 当作 selected skill 的局部高亮上下文。",
            ]
        ),
        render_paragraph(
            part(
                "rescue_candidate_pool_size = min(len(ranked), max(10, k * 4))",
                mono=True,
            )
        ),
        render_paragraph(
            [
                "紧接着，retriever 从排序列表前 ",
                part("rescue_candidate_pool_size", mono=True),
                " 个技能中，排除已 selected 的技能，把仍然带有高质量 ",
                part("top_subunits", mono=True),
                " 的候选技能收进 ",
                part("rescue_candidate_skill_records", mono=True),
                "。这一步本身还没有决定谁会被真正 rescue，只是给下游 affiliation/rescue 阶段准备候选池。",
            ]
        ),
        render_paragraph("4. Rescue 选择", style="Heading1"),
        render_paragraph(
            [
                "rescue 选择由 ",
                part("experiments/retrieval_tasks_backend/rescue_selector.py", mono=True),
                " 中的 ",
                part("select_rescued_subunits(...)", mono=True),
                " 完成。这个函数只在 ",
                part("topk_context_selected_plus_rescue", mono=True),
                " 和 ",
                part("topk_context_selected_affiliated_rescue", mono=True),
                " 两个 mode 下启用。",
            ]
        ),
        render_paragraph(
            [
                "它读取 ",
                part("selected_skill_ids", mono=True),
                "、selected skill 已经高亮的子单元文本，以及 ",
                part("rescue_candidate_skill_records", mono=True),
                "，然后按固定阈值和预算筛出真正值得 rescue 的 subunit。",
            ]
        ),
        render_paragraph(
            part(
                "RescueConfig: min_parent_score=0.35, min_subunit_score=0.12, max_global_rescues=3, max_per_parent=1, redundancy_threshold=0.6",
                mono=True,
            )
        ),
        render_paragraph(
            [
                part("1) ", bold=True),
                "跳过已经是 selected 的技能；parent skill 必须满足 ",
                part("final_score >= 0.35", mono=True),
                "。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("2) ", bold=True),
                "候选 subunit 必须满足 ",
                part("subunit_score >= 0.12", mono=True),
                " 且文本非空。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("3) ", bold=True),
                "如果 subunit 文本与 selected skill 已有高亮文本或已 rescue 文本的 Jaccard 重叠达到 ",
                part("0.6", mono=True),
                "，则视为冗余，直接丢弃。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("4) ", bold=True),
                "全局最多保留 ",
                part("3", mono=True),
                " 个 rescued subunits，每个非选中 parent 最多贡献 ",
                part("1", mono=True),
                " 个 rescued subunit。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                "输出结果是若干 rescued rows，字段包含 ",
                part("source_skill_id", mono=True),
                "、",
                part("source_graph_skill_id", mono=True),
                "、",
                part("parent_final_score", mono=True),
                "、",
                part("subunit_id", mono=True),
                "、",
                part("subunit_text", mono=True),
                "、",
                part("subunit_score", mono=True),
                " 和 ",
                part("subunit_similarity", mono=True),
                "。这些 rows 在 affiliated mode 下不会直接写进 context skill 正文，而是继续交给 affiliation 分配阶段。",
            ]
        ),
        render_paragraph("5. Affiliation 分配", style="Heading1"),
        render_paragraph(
            [
                "affiliation 阶段位于 ",
                part("affiliation_exposure.py", mono=True),
                " 的 ",
                part("build_affiliation_artifacts(...)", mono=True),
                "。它的目标不是把 rescue 信息变成新的 selected skill，而是把非选中技能的高价值子单元重新分配到最合适的 selected skill 名下，形成局部提示 cue。",
            ]
        ),
        render_paragraph(
            part(
                "AffiliationConfig: active_l2_limit=2, min_affiliation_score=0.12, min_bridge_score=0.16, bridge_margin=0.03, max_visible_cues_per_skill=2, redundancy_threshold=0.6",
                mono=True,
            )
        ),
        render_paragraph(
            [
                part("第一步：选 active L2。", bold=True),
                "代码会按 selected skills 的 ",
                part("final_score", mono=True),
                " 汇总对应 L2 权重，选出得分最高的 ",
                part("2", mono=True),
                " 个 L2 cluster 作为当前任务的 active L2。它们随后决定 community consistency 和额外惩罚项。",
            ]
        ),
        render_paragraph(
            [
                part("第二步：为每个 rescued subunit 找最合适的 selected parent。", bold=True),
                "对每个 rescued row，代码会把它和每个 selected skill 做一轮 scoring。核心特征是：",
                part("q_rel", mono=True),
                "（cue 与 task instruction 的 Jaccard 相似度）、",
                part("parent_fit", mono=True),
                "（cue 与 selected skill profile / highlighted subunits 的重合度）、",
                part("selected_prior", mono=True),
                "（selected skill 的归一化 final score）、",
                part("graph_support", mono=True),
                "（cue parent 与 selected skill 是否同 L2）、",
                part("community_consistency", mono=True),
                "（cue L2 是否属于 active L2）。",
            ]
        ),
        render_paragraph(
            part(
                "score = 0.15 * q_rel + 0.45 * parent_fit + 0.10 * selected_prior + 0.15 * graph_support + 0.15 * community_consistency",
                mono=True,
            )
        ),
        render_paragraph(
            part(
                "affiliation_score = top_score + 0.10 * exclusivity_bonus; if rescue_l2 not in active_l2: affiliation_score -= 0.08",
                mono=True,
            )
        ),
        render_paragraph(
            [
                "这里 ",
                part("exclusivity_bonus", mono=True),
                " 是第一名和第二名 parent score 的差值下界，用来奖励“这个 cue 明显只适合某一个 selected skill”的情况。若 ",
                part("affiliation_score < 0.12", mono=True),
                "，cue 会被打入 ",
                part("dropped_rescued_subunits", mono=True),
                "。",
            ]
        ),
        render_paragraph(
            [
                "桥接关系 ",
                part("bridge_skill_id", mono=True),
                " 的判定条件是：第二名 parent 存在、",
                part("second_score >= 0.16", mono=True),
                "，且 ",
                part("(top_score - second_score) <= 0.03", mono=True),
                "。这说明 cue 不只是单点挂靠，还可能对另一个 selected skill 有弱相关性。",
            ]
        ),
        render_paragraph(
            [
                "第三步：排序和预算控制。所有候选 cue 按 ",
                part("affiliation_score", mono=True),
                " 从高到低排序；每个 selected skill 最多显示 ",
                part("2", mono=True),
                " 个 cue；若新 cue 与该 selected skill 已挂载 cue 的 Jaccard 重叠超过 ",
                part("0.6", mono=True),
                "，则标记为冗余并丢弃。",
            ]
        ),
        render_paragraph(
            [
                "最后得到三类核心产物：",
                part("cues_by_skill_id", mono=True),
                "、",
                part("attached_rescued_subunits", mono=True),
                " 和 ",
                part("dropped_rescued_subunits", mono=True),
                "。它们分别代表“每个 selected skill 实际挂到了什么 cue”、“哪些 rescue 成功附着”、“哪些 rescue 被阈值或预算规则淘汰”。",
            ]
        ),
        render_paragraph("6. 物化产物", style="Heading1"),
        render_paragraph(
            [
                "在 ",
                part("materialize_task_mirror(...)", mono=True),
                " 里，",
                part("topk_context_selected_affiliated_rescue", mono=True),
                " 会先复制 selected skills，再调用 ",
                part("create_affiliation_context_skill(...)", mono=True),
                " 生成一个 task-local coordinator。这个 coordinator 的 frontmatter 中 ",
                part("artifact_kind", mono=True),
                " 固定为 ",
                part("affiliation_context_skill", mono=True),
                "，这点也被 ",
                part("tests/test_llm_topk_smoke.py", mono=True),
                " 明确断言。",
            ]
        ),
        render_paragraph(
            [
                "产物可以分为四层：",
            ]
        ),
        render_paragraph(
            [
                part("1) selected skills", bold=True),
                "：原始 selected skill 目录会被复制到 ",
                part("environment/skills/<skill_id>", mono=True),
                "，它们仍是主执行面，也是事实冲突时的优先来源。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("2) affiliation coordinator", bold=True),
                "：生成的新技能名称形如 ",
                part("affiliation-coordinator-<task>-<hash>", mono=True),
                "，正文由 ",
                part("compile_affiliation_coordinator_markdown(...)", mono=True),
                " 生成，只提供 skill routing index，不直接展开 rescued subunit 大段正文。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("3) affiliated cue sidecars", bold=True),
                "：每个真正收到 cue 的 selected skill 会在其目录下生成 ",
                part(".affiliation/AFFILIATED_CUES.md", mono=True),
                "。文件内按执行线索、对象参数、注意事项三类分组，并注明 cue 来自哪个 non-selected skill。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                part("4) task-level manifest", bold=True),
                "：任务根目录下写出 ",
                part("affiliation_manifest.json", mono=True),
                "，其中记录 ",
                part("selected_skill_ids", mono=True),
                "、",
                part("active_l2_ids", mono=True),
                "、",
                part("active_l2_labels", mono=True),
                "、",
                part("affiliated_cue_files", mono=True),
                "、",
                part("attached_rescued_subunits", mono=True),
                " 和 ",
                part("dropped_rescued_subunits", mono=True),
                "。",
            ],
            bullet=True,
        ),
        render_paragraph(
            [
                "除此之外，mirror 总清单 ",
                part("mirror_manifest.json", mono=True),
                " 还会把 ",
                part("retrieved_skills_ranked", mono=True),
                "、",
                part("retrieval_metadata", mono=True),
                "、",
                part("post_rerank_retrieved_skills_ranked", mono=True),
                "、",
                part("generated_skill_kind", mono=True),
                "、",
                part("affiliated_cue_counts", mono=True),
                " 和 ",
                part("affiliation_manifest_path", mono=True),
                " 一起落盘，便于后续分析脚本直接消费。",
            ]
        ),
        render_paragraph(
            [
                "执行层产物则由 runner 统一收口：每个任务都有一个 Harbor job 目录；批量层面会得到 ",
                part("summary.jsonl", mono=True),
                "、",
                part("summary.txt", mono=True),
                "、",
                part("rows/*.json", mono=True),
                " 和 ",
                part("rows_txt/*.txt", mono=True),
                "。这些文件负责记录 reward、phase duration、token 和异常摘要，不改变 affiliated mode 的算法本质。",
            ]
        ),
        render_paragraph(
            [
                part("重要说明：", bold=True),
                part("post_retrieval_rerank", mono=True),
                " 只是位于检索与物化之间的可选 hook，默认关闭。它不是 affiliated rescue 的默认核心算法，也不改变 affiliation 阶段关于 active L2、cue 挂载和 sidecar 生成的基本规则。",
            ]
        ),
        render_paragraph("7. 与相邻模式对比", style="Heading1"),
        render_paragraph(
            [
                "为了看清 affiliated rescue 的设计意图，最有必要对比的相邻模式是 ",
                part("topk_context_selected_plus_rescue", mono=True),
                "。两者共享上游检索和 rescue 候选筛选，但对 rescue 内容的落点设计完全不同。",
            ]
        ),
        render_table(compare_rows, [1800, 3600, 3600], header_rows=1),
        render_paragraph(
            [
                "测试也体现了这一点：",
                part("test_topk_context_selected_affiliated_rescue_materializes_local_affiliation", mono=True),
                " 会断言 coordinator 文本中出现 ",
                part("AFFILIATED_CUES.md", mono=True),
                "，并且 ",
                part("affiliation_manifest.json", mono=True),
                " 中存在 ",
                part("attached_rescued_subunits", mono=True),
                " 和 sidecar path；同时它明确断言 selected skills 仍是主执行面，而 rescue 内容只是 local affiliation。",
            ]
        ),
        render_paragraph(
            [
                "一句话总结：",
                part("topk_context_selected_affiliated_rescue", mono=True),
                " 的核心不是“把更多技能选进来”，而是“在不扩张 selected skill 集的前提下，把非选中技能里最有价值、最贴近当前 selected parent 的局部线索以 affiliated cue 的形式挂靠进去”。这使得系统保留了 selected skills 的主界面与可解释性，同时又吸收了部分非选中技能的高价值补充信息。",
            ]
        ),
    ]
    return blocks


def build_document_xml(template_document_xml: str) -> str:
    prefix, body = template_document_xml.split("<w:body>", 1)
    match = re.search(r"(<w:sectPr.*</w:sectPr>)</w:body></w:document>$", body, re.S)
    if not match:
        raise RuntimeError("Failed to locate template section properties in word/document.xml")
    sect_pr = match.group(1)
    body_xml = "".join(build_blocks()) + sect_pr
    return f"{prefix}<w:body>{body_xml}</w:body></w:document>"


def unpack_template(template_docx: Path, dest_dir: Path) -> None:
    with zipfile.ZipFile(template_docx, "r") as archive:
        archive.extractall(dest_dir)


def pack_fallback(source_dir: Path, output_docx: Path) -> None:
    output_docx.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_docx, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir))


def pack_document(source_dir: Path, output_docx: Path) -> tuple[str, str]:
    result = subprocess.run(
        [sys.executable, str(PACK_SCRIPT), str(source_dir), str(output_docx)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "pack.py", (result.stdout + result.stderr).strip()
    pack_fallback(source_dir, output_docx)
    fallback_reason = (result.stdout + result.stderr).strip()
    if "ModuleNotFoundError: No module named 'defusedxml'" in fallback_reason:
        fallback_reason = "pack.py unavailable: missing defusedxml; used built-in zipfile fallback"
    elif fallback_reason:
        fallback_reason = f"pack.py failed; used built-in zipfile fallback\n{fallback_reason}"
    else:
        fallback_reason = "pack.py failed; used built-in zipfile fallback"
    return "zipfile-fallback", fallback_reason


def validate_docx(docx_path: Path) -> tuple[bool, str]:
    if shutil.which("soffice"):
        with tempfile.TemporaryDirectory(prefix="affiliated-report-validate-") as tmpdir:
            out_dir = Path(tmpdir)
            result = subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--convert-to",
                    "html:HTML",
                    "--outdir",
                    str(out_dir),
                    str(docx_path),
                ],
                capture_output=True,
                text=True,
            )
            html_path = out_dir / f"{docx_path.stem}.html"
            ok = result.returncode == 0 and html_path.exists()
            message = (result.stdout + result.stderr).strip()
            if ok:
                return True, message or f"Validated via soffice -> {html_path}"
            return False, message or "soffice validation failed"
    try:
        with zipfile.ZipFile(docx_path, "r") as archive:
            required = {"[Content_Types].xml", "word/document.xml", "word/styles.xml", "word/numbering.xml"}
            names = set(archive.namelist())
        missing = sorted(required - names)
        return (not missing), ("zip structure ok" if not missing else f"missing entries: {missing}")
    except Exception as exc:
        return False, f"zip validation failed: {type(exc).__name__}: {exc}"


def generate_report(template_docx: Path, output_docx: Path) -> tuple[str, bool, str]:
    with tempfile.TemporaryDirectory(prefix="affiliated-report-build-") as tmpdir:
        unpacked_dir = Path(tmpdir) / "unpacked"
        unpack_template(template_docx, unpacked_dir)
        document_xml_path = unpacked_dir / "word" / "document.xml"
        template_document_xml = document_xml_path.read_text(encoding="utf-8")
        document_xml_path.write_text(build_document_xml(template_document_xml), encoding="utf-8")
        pack_mode, pack_log = pack_document(unpacked_dir, output_docx)
    valid, validation_log = validate_docx(output_docx)
    extra = pack_log.strip()
    if extra:
        validation_log = f"{validation_log}\n{extra}".strip()
    return pack_mode, valid, validation_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the affiliated rescue algorithm Word report.")
    parser.add_argument("--template", type=Path, default=TEMPLATE_DOCX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.template.is_file():
        raise SystemExit(f"Template not found: {args.template}")
    pack_mode, valid, validation_log = generate_report(args.template, args.output)
    print(f"output={args.output}")
    print(f"pack_mode={pack_mode}")
    print(f"validated={int(valid)}")
    if validation_log:
        print(validation_log)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
