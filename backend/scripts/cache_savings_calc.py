# -*- coding: utf-8 -*-
"""v0.4.2 缓存优化 token 节省估算（对比计算）。

口径：TokenEstimator（len/3.5 字符近似，中英混合，±20% 误差可接受）。
计算「优化前（整条 system 一个字符串，技能/记忆变了缓存全失效）」vs
「优化后（稳定段逐字节不变，前缀缓存稳定命中）」的差异。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hemlock.core.context_manager import TokenEstimator as TE
from hemlock.core.skill_registry import build_skills_prompt
from hemlock.core.memory_manager import build_memories_prompt, list_memories
from hemlock.core.database import get_db
from hemlock.services.agent_service import SYSTEM_PROMPT, AUTO_PENTEST_PROMPT
from hemlock.core.config import settings


def estimate(text: str) -> int:
    return TE.estimate(text)


def _sim_memories(n: int) -> str:
    """模拟 n 条记忆的索引文本（name + desc + trigger，贴近真实规模）。"""
    lines = ["\n\n## 长期记忆索引 (Agent Memories Index)\n"
             "以下是跨会话保留的记忆索引。相关记忆与你当前任务有关时，"
             "用 get_memory 工具读取其完整内容：\n"]
    for i in range(n):
        lines.append(f"- **target-auth-memory-{i}** [feedback] 目标系统的认证绕过经验与坑位记录第 {i} 条")
        lines.append(f"   触发词: 认证, 登录, token, session")
    return "\n".join(lines)


def main() -> None:
    db = next(get_db(), None)

    stable = SYSTEM_PROMPT + AUTO_PENTEST_PROMPT
    skills = build_skills_prompt(db)
    real_mem = build_memories_prompt(db)
    mcp = ""

    stable_tk = estimate(stable)
    skills_tk = estimate(skills)
    real_mem_tk = estimate(real_mem)
    dyn_tk = skills_tk + real_mem_tk

    print("=" * 64)
    print("Hemlock v0.4.2 缓存优化 - token 对比计算")
    print("口径: TokenEstimator(len/3.5)，中英混合 ±20% 误差")
    print("=" * 64)
    print(f"\n[段构成 - 当前实测]")
    print(f"  稳定段 SYSTEM_PROMPT+AUTO_PENTEST : {stable_tk:>6} tokens")
    print(f"  技能索引                          : {skills_tk:>6} tokens")
    print(f"  记忆索引 (当前 {len(list_memories(db))} 条)          : {real_mem_tk:>6} tokens")
    print(f"  动态段合计                        : {dyn_tk:>6} tokens")

    # ===== 核心对比 =====
    print("\n" + "=" * 64)
    print("[为什么优化? 优化前后单轮对比]")
    print("=" * 64)
    print(f"""
  优化前: 整条 system 一个字符串 ({stable_tk + dyn_tk} tokens)
    技能/记忆任意变化 -> 前缀缓存【从变化点起全部失效】, 整条重算
  优化后: 稳定段 {stable_tk} tokens 在前, 动态段 {dyn_tk} tokens 在后
    技能/记忆任意变化 -> 只影响断点后的动态段, 稳定段【永远命中】
""")

    # ===== 多轮对比（不同记忆量 / 不同会话长度）=====
    # 缓存命中价格倍率（DeepSeek-V4-Flash 实测口径，2026-08）：
    #   命中输入 0.02元/M vs 未命中输入 1元/M → 命中 = 未命中的 1/50
    HIT_RATIO = 1 / 50  # Flash；Pro 为 1/120（这里用保守的 Flash）

    print("=" * 64)
    print("[累计对比: 优化前 vs 优化后 的输入 token 总量]")
    print("=" * 64)
    print(f"(缓存命中计费倍率: 1/{int(1 / HIT_RATIO)} —— DeepSeek-V4-Flash 实测)")

    print(f"\n{'记忆条数':<8}{'会话轮数':<8}{'优化前/轮':<11}{'优化后输入/轮':<13}{'输入费节省':<10}{'计费节省':<8}")
    print("-" * 74)
    for n_mem in (0, 10, 30, 50):
        mem_tk = estimate(_sim_memories(n_mem))
        sd_tk = stable_tk
        dd_tk = skills_tk + mem_tk
        for rounds in (5, 10, 20):
            old_sys = sd_tk + dd_tk
            old_total = old_sys * rounds
            # 优化前: 每轮整条 system 全额计费
            # 优化后: 动态段全额 + 稳定段按缓存命中价(1/50)
            new_cost_tokens = dd_tk * rounds + sd_tk * rounds * HIT_RATIO
            old_cost_tokens = old_total
            save_pct = (old_cost_tokens - new_cost_tokens) / old_cost_tokens * 100
            print(f"{n_mem:<8}{rounds:<8}{old_sys:<11}{dd_tk:<13}{old_cost_tokens - new_cost_tokens:<10.0f}{save_pct:<7.0f}%")

    print(f"""
  注: 计费节省 = (优化前全额 - 优化后) / 优化前全额
  优化前 = 整条 system 每轮全额计费 (技能/记忆一变, 前缀全失效重算)
  优化后 = 动态段每轮全额 + 稳定段按缓存命中价(1/{int(1 / HIT_RATIO)})
  行中『优化前/轮』= 优化前每轮整条 system 的 token; 『优化后输入/轮』= 仅动态段
""")

    db.close() if hasattr(db, "close") else None


if __name__ == "__main__":
    main()
