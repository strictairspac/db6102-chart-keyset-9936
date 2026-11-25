import { api } from "../api/workbench.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { status } from "../components/common/status.js";
import { table } from "../components/common/table.js";
import { poll } from "../hooks/poll.js";
import { el } from "../utils/dom.js";
import { time } from "../utils/format.js";

const PENDING = "queued";
const WITHDRAWABLE = new Set(["queued", "done"]);

function describe(run) {
  return {
    queued: "后台正在处理，回执待领，页面会自动更新。",
    done: "回执已就绪，可以领取结果。",
    failed: run.failure || "本次计算没有完成。",
    cancelled: "回执已撤销，不再领取本次计算的结果。",
  }[run.status];
}

function receiptBadge(run) {
  return el("span", {"data-receipt": run.id}, status(run.status));
}

function reportState(run, onCancel) {
  return el("div", {class: "stack", id: "run-state"},
    el("p", {class: "receipt-line", "data-testid": "receipt-state"},
      el("span", {}, "我的回执："), status(run.status)),
    el("p", {}, describe(run)),
    run.status === "done" ? el("a", {class: "button", href: `#evidence/${run.id}`}, "查看业务证据") : null,
    WITHDRAWABLE.has(run.status) ?
      el("button", {type: "button", class: "quiet", "data-testid": "withdraw", onclick: () => onCancel(run.id)},
        "撤销回执") : null);
}

export async function runsPage(config, route, signal) {
  const items = await api.runs();
  const latest = route.id ? await api.run(route.id, signal) : items[0];
  const detail = latest ? reportState(latest, cancel) : empty("进入资料页，选用一个业务样本并提交执行。");

  function show(next) {
    detail.replaceChildren(...reportState(next, cancel).childNodes);
    const badge = document.querySelector(`[data-receipt="${next.id}"]`);
    if (badge) badge.replaceWith(receiptBadge(next));
  }

  async function cancel(id) {
    try {
      show(await api.cancel(id));
    } catch (error) {
      detail.append(el("p", {role: "alert"}, error.message));
    }
  }

  async function refresh() {
    try {
      const next = await api.run(latest.id, signal);
      if (!signal.aborted) show(next);
      return next.status === PENDING;
    } catch (error) {
      if (!signal.aborted) detail.append(el("p", {role: "alert"}, error.message));
      return false;
    }
  }

  if (latest && latest.status === PENDING) poll(refresh, signal);
  return el("div", {class: "page"},
    pageHeading(config.modules[2], config.parameter_label),
    latest ? section(latest.case_title, detail) : detail,
    section("执行回执", items.length ? table(["资料", "回执", "参数", "状态", "提交时间"],
      items.map(run => [el("a", {href: `#runs/${run.id}`}, run.case_title), run.id,
        run.parameter, receiptBadge(run), time(run.created_at)]), "执行回执表") :
      empty("暂无执行回执。")));
}
