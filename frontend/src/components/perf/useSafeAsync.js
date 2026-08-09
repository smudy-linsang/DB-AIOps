import { useEffect, useRef } from 'react';

/**
 * BUG-133: 性能中心各 Tab 的数据加载 effect 此前既无 cleanup 也无取消机制。
 * 开启 10s 自动刷新并快速切换时间窗时，慢的旧请求会在新请求之后返回，
 * 用旧数据覆盖新数据 —— 用户切到"30分钟"，图表却显示"7天"的内容。
 *
 * 用法：
 *   const runSafe = useSafeAsync();
 *   useEffect(() => runSafe((alive) => {
 *     perfAPI.aas(id, range).then((r) => alive() && setAas(r.data));
 *   }), [id, range]);
 *
 * 更常用的是直接在 effect 里判活，见 withAlive。
 */
export default function useSafeAsync() {
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  return (fn) => fn(() => mounted.current);
}

/**
 * 把一次异步加载包装成"只有最后一次发起的调用才允许写 state"。
 * 返回值可直接作为 useEffect 的返回（cleanup）。
 *
 *   useEffect(() => withAlive((alive) => {
 *     load().then((d) => { if (alive()) setData(d); });
 *   }), [deps]);
 */
export function withAlive(fn) {
  let alive = true;
  fn(() => alive);
  return () => { alive = false; };
}

/**
 * 统一的性能中心错误文案：后端 err() 会带 code（见 api_views_perf.PerfBaseView），
 * 对可操作的错误码给出具体指引，而不是把原始 message 甩给用户。
 */
export function fmtPerfError(action, e) {
  const code = e?.code;
  if (code === 'TSDB_DOWN' || code === 'TSDB_ERROR') {
    return `${action}失败：时序库不可用，请检查 TimescaleDB 连接与 start_sentinel 进程`;
  }
  if (code === 'FORBIDDEN') {
    return `${action}失败：当前账号无此权限，请联系管理员`;
  }
  if (code === 'NOT_FOUND') {
    return `${action}失败：实例不存在或不在你的可访问范围内`;
  }
  if (code === '40001') {
    return `${action}失败：所选时间窗已超出 ASH 明细保留期（7天），请缩小范围`;
  }
  return `${action}失败：${e?.message || '未知错误'}`;
}
