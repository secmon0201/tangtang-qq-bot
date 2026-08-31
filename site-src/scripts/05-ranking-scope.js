const scopeCopy = {
  acoast: "<strong>A海岸五群合榜</strong><br>在五个 A海岸群中请求默认排行时，将五群玩家合并比较；这不是所有机器人群的总榜。",
  managed: "<strong>bot 全部有效群排行</strong><br>在其他受管理群发出默认排行时，比较所有机器人有效群；命令中加 bot 也可显式请求这一范围。",
  current: "<strong>仅当前群排行</strong><br>命令中加入“群”时，不论从哪个群发出，都只查看发起群内部排行。"
};
const scopeExplain = document.querySelector("[data-scope-explain]");
document.querySelectorAll("[data-scope]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-scope]").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
  if (scopeExplain) scopeExplain.innerHTML = scopeCopy[button.dataset.scope];
}));
