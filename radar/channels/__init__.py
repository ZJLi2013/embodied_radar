from radar.channels import github, hf, page, rss

COLLECTORS = {"rss": rss.collect, "page": page.collect, "github": github.collect, "hf": hf.collect}
