from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from .models import DatabaseConfig, MonitorLog
import json

def dashboard(request):
    """
    监控大屏主页
    """
    databases = DatabaseConfig.objects.filter(is_active=True)
    dashboard_data = []
    
    for db in databases:
        latest_log = MonitorLog.objects.filter(config=db).order_by('-create_time').first()
        info = {
            'id': db.id,
            'name': db.name,
            'host': f"{db.host}:{db.port}",
            'type': db.get_db_type_display(),
            'status': '未知',
            'check_time': '无数据',
            'details': {}
        }
        if latest_log:
            info['status'] = latest_log.status
            info['check_time'] = latest_log.create_time
            try:
                info['details'] = json.loads(latest_log.message)
            except:
                info['details'] = {}
        dashboard_data.append(info)
        
    return render(request, 'monitor/dashboard.html', {'data': dashboard_data})

def detail(request, config_id):
    """
    详情页：连接数趋势 + 表空间当前值 + [新增] 表空间历史趋势
    """
    config = get_object_or_404(DatabaseConfig, id=config_id)
    
    # 1. 取出最近 50 条日志
    logs = list(MonitorLog.objects.filter(config=config).order_by('-create_time')[:50])
    # 反转顺序，让时间轴从旧到新
    logs.reverse()
    
    dates = []
    connections = []
    
    # [新增] 用于存储表空间趋势数据的字典
    # 结构: {'SYSTEM': [80, 81, ...], 'USERS': [10, 10, ...]}
    tbs_trend_map = {}
    
    # 第一遍遍历：收集所有出现过的表空间名字 (防止有的时间点有，有的没有)
    all_tbs_names = set()
    for log in logs:
        try:
            data = json.loads(log.message)
            if 'tablespaces' in data:
                for tbs in data['tablespaces']:
                    all_tbs_names.add(tbs['name'])
        except:
            pass

    # 初始化趋势列表
    for name in all_tbs_names:
        tbs_trend_map[name] = []

    # 第二遍遍历：填充数据
    for log in logs:
        local_time = timezone.localtime(log.create_time)
        dates.append(local_time.strftime("%H:%M"))
        
        # 解析日志内容
        data = {}
        try:
            data = json.loads(log.message)
        except:
            pass
            
        # 1. 连接数
        connections.append(data.get('active_connections', 0))
        
        # 2. 表空间历史数据填充
        # 先把当前日志里的表空间转成字典方便查询: {'SYSTEM': 88.5, 'USERS': 20}
        current_tbs_map = {}
        if 'tablespaces' in data:
            for tbs in data['tablespaces']:
                current_tbs_map[tbs['name']] = tbs['used_pct']
        
        # 遍历所有已知的表空间，如果当前日志里有就填值，没有就填 None
        for name in all_tbs_names:
            val = current_tbs_map.get(name, None) # 如果某个时间点没采到，填None，图表会断开
            tbs_trend_map[name].append(val)

    # 3. 提取最新一次的表空间数据 (用于画进度条卡片)
    latest_tablespaces = []
    if logs:
        try:
            latest_data = json.loads(logs[-1].message) # logs[-1] 是最新的
            latest_tablespaces = latest_data.get('tablespaces', [])
        except:
            pass

    context = {
        'config': config,
        'dates': json.dumps(dates),
        'connections': json.dumps(connections),
        'tablespaces': latest_tablespaces,   # 传给进度条
        'tbs_trend_map': json.dumps(tbs_trend_map) # [新增] 传给折线图
    }
    
    return render(request, 'monitor/detail.html', context)