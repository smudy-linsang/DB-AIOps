import pymysql
import sys

# ---------------------------------------------------------
# 这里填写你要测试的 MySQL 数据库信息
# ---------------------------------------------------------
DB_HOST = '127.0.0.1'  # 数据库IP
DB_PORT = 3306         # 端口
DB_USER = 'root'       # 用户名
DB_PASS = 'root123'   # 密码 (请修改为你真实的密码)
DB_NAME = 'testdb'      # 默认连接的库名
# ---------------------------------------------------------

def check_mysql_status():
    print(f"正在尝试连接 MySQL ({DB_HOST})...")
    
    try:
        # 1. 建立连接
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            connect_timeout=5
        )
        
        # 2. 获取游标
        cursor = conn.cursor()
        
        # 3. 执行 SQL (作为DBA，你对这个很熟悉)
        # 这是一个简单的查询，查看数据库版本和运行时间
        sql = "SHOW GLOBAL STATUS LIKE 'Uptime';"
        cursor.execute(sql)
        result = cursor.fetchone() # 获取结果
        
        sql_ver = "SELECT VERSION();"
        cursor.execute(sql_ver)
        version = cursor.fetchone()

        print("-" * 30)
        print("[OK] 连接成功！")
        print(f"数据库版本: {version[0]}")
        print(f"运行时间(秒): {result[1]}")
        print("-" * 30)

        # 4. 关闭连接
        cursor.close()
        conn.close()

    except Exception as e:
        print("-" * 30)
        print("❌ 连接失败！")
        print(f"错误信息: {e}")
        print("-" * 30)

if __name__ == "__main__":
    check_mysql_status()