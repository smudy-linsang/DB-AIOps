import '@testing-library/jest-dom'

// jsdom 不实现 matchMedia，antd 的部分组件依赖它
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// 每个用例后清理 localStorage，防止测试间状态泄露
afterEach(() => {
  localStorage.clear()
})
