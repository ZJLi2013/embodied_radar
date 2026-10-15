# embodied_radar

radar to track latest embodied fields and feedback insights

按周触发的领域技术雷达：跟踪一份可配置的头部团队清单，从每个团队的官方渠道（RSS、博客页面、GitHub 组织、Hugging Face 组织）取出本周新条目，辅以开放搜索发现清单外的团队，写进持续维护的 wiki，出一份「本周变化」周报。首个领域是具身机器人；领域只是一份配置，换领域不改代码。

- 团队与渠道：`domains/<domain>/entities.yaml`，增删团队只改这一个文件。
- 采集：`python -m radar.collect --domain embodied`，输出 `out/<domain>/latest/{items.jsonl,summary.json}`，渠道快照写入 `state/<domain>/`。
- 每周一由 GitHub Actions 运行（`.github/workflows/radar-collect.yml`），把 `state/<domain>/` 与本轮候选 `history/<domain>/<日期>-<run_id>/` 放在同一个提交里推回 `main`。
- 评测标注：`evals/<domain>/gold.yaml`，运行时不读取。

开发中。
