# embodied_radar

Weekly radar for new technical work from leading embodied-AI teams, built from their official channels.

按周运行的领域技术雷达：跟踪一份头部团队清单，从每个团队的官方渠道取出本周新条目，用 LLM 判断是否属于范围内的技术工作，写进持续维护的 wiki，并出一份「本周变化」周报。首个领域是具身机器人，聚焦「大脑」（算法、模型）与数据 / 仿真基础设施；领域只是一份配置，换领域不改代码。

## 最近一期

<!-- latest:embodied -->
2026-10-15（窗口 2026-10-08 至 2026-10-15）新增 4 条 · [完整周报](reports/embodied/2026-10-15-38688104123.md) · [全部条目](wiki/embodied/index.md)

### 大脑
- 2026-10-13 · [Gemini 4 Argon for Robotics](https://deepmind.google/blog/gemini-4-argon-for-robotics/) — Google DeepMind Robotics · rss
  > 正文披露了 Gemini 4 Argon 这一面向机器人物理推理的模型及其在机器人控制、世界模型、仿真数字孪生上的新技术能力。
- 2026-10-10 · [imprint](https://github.com/amazon-far/imprint) — Amazon FAR · github
  > IMPRINT 是面向 VLA 策略的长时程记忆模块，属于机器人「大脑」的策略学习与控制算法，并发布了模型、数据与仿真评测基础设施。
- 2026-10-08 · [robo_jepa](https://github.com/facebookresearch/robo_jepa) — Meta FAIR · github
  > RoboJEPA 是 Meta FAIR 发布的机器人潜在世界模型，包含预训练模型、代码与规划算法，属于机器人「大脑」范畴。

### 数据 / 仿真
- 2026-10-12 · [lerobot/FineART](https://huggingface.co/datasets/lerobot/FineART) — Hugging Face LeRobot · hf
  > FineART 是面向机器人双臂操作的数据集，含密集子任务标注，并配套发布 FineART-VLA 策略模型，属于机器人数据与 VLA 技术工作。
<!-- /latest:embodied -->

## 产出

- **周报**：[`reports/embodied/`](reports/embodied/)，每次运行一份 `<日期>-<run_id>.md`，只列本次新增条目，以及待人工确认、判定失败与暂缓的条目。
- **Wiki**：[`wiki/embodied/index.md`](wiki/embodied/index.md) 与 `wiki/embodied/teams/<team>.md`，从第一周起累积全部保留条目。`entries.jsonl` 只追加，页面每次由它重新生成。

## 工作方式

每周一 02:00 UTC，GitHub Actions（[`.github/workflows/radar-collect.yml`](.github/workflows/radar-collect.yml)）依次执行：

1. **采集**：对 [`domains/embodied/entities.yaml`](domains/embodied/entities.yaml) 中每个团队的渠道取本周新条目，窗口为执行日前 7 天。四种渠道都是确定性工具：
   - `rss`：公司级 feed 或团队论文 feed；
   - `page`：博客 / 团队列表页的链接快照比对；
   - `github`：组织下本周新建的仓库；
   - `hf`：Hugging Face 组织下本周新建的模型与数据集。
2. **范围判定**：每个候选一次 DeepSeek 调用，依据页面正文判 keep / reject / 待人工，范围规则写在 [`domains/embodied/domain.yaml`](domains/embodied/domain.yaml)。每周有请求数、token 与时长上限，超限或失败的条目留到下次运行先判。
3. **写 wiki 与周报**，刷新本页「最近一期」，并把渠道状态（`state/`）、本轮候选（`history/`）、`wiki/`、`reports/` 与本页放在同一个 bot 提交里推回 `main`。

## 运行

在自己的 fork 里运行：

1. 在仓库 Settings → Secrets 设置 `DEEPSEEK_API_KEY`。
2. 启用 Actions。定时运行自动开始；也可以在 Actions 页面手动触发 `radar-collect`，`domain` 留空即为 `embodied`。

本地运行（Python ≥ 3.11）：

```bash
pip install ".[test]" ./LLMProvider
pytest
python -m radar.collect --domain embodied   # 输出 out/embodied/latest/，更新 state/embodied/
DEEPSEEK_API_KEY=... DEEPSEEK_MODEL=deepseek-chat python -m radar.weekly --domain embodied
```

`GITHUB_TOKEN` 可选，用于提高 GitHub API 限额。

## 新增领域

复制 `domains/embodied/` 为 `domains/<domain>/`，改写：

- `entities.yaml`：团队清单与渠道，增删团队只改这个文件；
- `domain.yaml`：范围规则、页面日期抓取预算、判定上限；
- `queries.yaml`：补充搜索的查询。

运行时传 `--domain <domain>`，workflow 手动触发时填 `domain`。

## 已知局限

- 只看官方渠道，不收 arXiv、X、公众号。国内团队主要在公众号发布，目前覆盖不到。
- 前端渲染的页面取不到链接（如 Meta 的 `/research/`），只能改用能直接解析的列表页。
- GitHub 只看新建仓库，不跟踪已有仓库的 release；Hugging Face 只看新建的模型与数据集。
- 带日期的渠道（RSS、GitHub、HF）按 7 天窗口判新，漏跑一周时这部分不会回补；`page` 渠道按快照比对，会补上。
- 范围判定在 31 条冻结样本上全对，但金标只有 1 条，召回率还测不了。
- 开放搜索（SearXNG）的结果目前只记入运行摘要，不进入判定与周报。

## Roadmap

- 确认首个周一定时运行能读回状态、增量更新 wiki 与周报。
- 把开放搜索发现的清单外团队放进周报的候选团队一节，人工确认后再加入 `entities.yaml`。
- 每收到一条漏报就追加一条金标，攒够后测召回率。
- 视需要跟踪旗舰仓库的 release。

## 参与贡献

欢迎：

- **增删团队或渠道**：只改 `entities.yaml`，PR 里附上渠道链接和该渠道最近的一条技术发布作为证据。
- **报告漏报或误判**：开 issue，附条目链接。漏报会作为金标进入 `evals/`。
- **渠道解析修复**：附上能复现问题的 fixture 与测试。
- **新领域配置**。

不收：

- 硬件本体厂商、融资 / 商务新闻、arXiv 论文源；
- 需要登录或违反站点条款的抓取。

约定：

- `state/`、`history/`、`wiki/`、`reports/` 由 workflow 生成，PR 里不要手改；要纠正某条判定，请开 issue。
- 提 PR 前 `pytest` 需全部通过。
- 用 AI 工具写的 PR，请在描述里写明用了哪个工具、参与到什么程度。

## License

代码与配置采用 [MIT License](LICENSE)。

README、`wiki/`、`reports/` 中收录的标题与链接指向原发布方，内容版权归原发布方。
