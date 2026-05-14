/**
 * ErrorBoundary - 全局错误边界组件
 * 捕获子组件渲染时的 JavaScript 错误，防止整个 React 组件树崩溃（白屏）
 */
import React from 'react';
import { Button, Result, Typography, Space } from 'antd';
import { ReloadOutlined, BugOutlined } from '@ant-design/icons';

const { Paragraph, Text } = Typography;

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    // 同时输出到控制台，方便调试
    console.error('[ErrorBoundary] 捕获到渲染错误:', error);
    console.error('[ErrorBoundary] 组件栈:', errorInfo?.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    // 尝试重新挂载
    if (this.props.onReset) {
      this.props.onReset();
    }
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      // 如果提供了自定义 fallback，使用它
      if (this.props.fallback) {
        return this.props.fallback;
      }

      const errorMessage = this.state.error?.message || '未知错误';
      const errorStack = this.state.error?.stack || '';
      const componentStack = this.state.errorInfo?.componentStack || '';

      return (
        <div style={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          minHeight: 400,
          padding: 24,
          background: '#f0f2f5',
        }}>
          <div style={{ maxWidth: 800, width: '100%' }}>
            <Result
              status="error"
              title="页面渲染出错"
              subTitle="页面组件在渲染过程中发生了未捕获的错误，请尝试刷新页面或联系管理员。"
              icon={<BugOutlined />}
              extra={
                <Space>
                  <Button type="primary" icon={<ReloadOutlined />} onClick={this.handleReload}>
                    刷新页面
                  </Button>
                  <Button onClick={this.handleReset}>
                    重试渲染
                  </Button>
                </Space>
              }
            />
            <div style={{
              marginTop: 16,
              padding: 16,
              background: '#fff',
              borderRadius: 8,
              border: '1px solid #ffd8bf',
              maxHeight: 300,
              overflow: 'auto',
            }}>
              <Text strong style={{ color: '#ff4d4f', display: 'block', marginBottom: 8 }}>
                错误详情（供开发人员参考）：
              </Text>
              <Paragraph copyable style={{ marginBottom: 8 }}>
                <Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {errorMessage}
                </Text>
              </Paragraph>
              {errorStack && (
                <>
                  <Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>
                    Stack Trace:
                  </Text>
                  <Paragraph style={{ marginBottom: 8 }}>
                    <pre style={{
                      fontSize: 11,
                      color: '#999',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-all',
                      maxHeight: 120,
                      overflow: 'auto',
                      margin: 0,
                    }}>
                      {errorStack}
                    </pre>
                  </Paragraph>
                </>
              )}
              {componentStack && (
                <>
                  <Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>
                    React 组件栈：
                  </Text>
                  <Paragraph>
                    <pre style={{
                      fontSize: 11,
                      color: '#999',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-all',
                      maxHeight: 120,
                      overflow: 'auto',
                      margin: 0,
                    }}>
                      {componentStack}
                    </pre>
                  </Paragraph>
                </>
              )}
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
