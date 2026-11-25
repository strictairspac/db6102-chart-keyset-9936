export const States = Object.freeze({
  queued: "待领", done: "已就绪", failed: "执行失败", cancelled: "已撤销",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败",
  review: "复核签署", cancel_run: "撤销回执", cancel_job: "撤销计算",
});
