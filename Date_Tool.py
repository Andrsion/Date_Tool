import datetime
import re
import requests
import json
import platform
import sys
import os
from colorama import init, Fore, Style
from tqdm import tqdm
import time
import random

# 初始化 colorama
init(autoreset=True)

# 颜色常量定义
COLORS = {
    'HEADER': Fore.LIGHTCYAN_EX,
    'SUCCESS': Fore.GREEN,
    'ERROR': Fore.RED,
    'WARNING': Fore.YELLOW,
    'INFO': Fore.BLUE,
    'RESET': Style.RESET_ALL
}

class DateQueryTool:
    """具体日期查询工具"""
    def __init__(self):
        self.holidays = {}
        self.current_year = None
    
    def get_system_time(self):
        """获取系统当前时间"""
        try:
            now = datetime.datetime.now()
            system_info = platform.system()
            print(f"{COLORS['SUCCESS']}成功获取系统时间：{now.strftime('%Y年%m月%d日')} ({system_info}){COLORS['RESET']}")
            return now.year
        except Exception as e:
            print(f"{COLORS['WARNING']}获取系统时间失败：{e}{COLORS['RESET']}")
            return None
    
    @staticmethod
    def validate_date_input(text):
        """验证日期输入格式"""
        pattern = re.compile(r'^([^\d]*)(\d{1,2})([^\d]+)(\d{1,2})([^\d]*)$')
        match = pattern.match(text.strip())
        if not match:
            return False, "输入格式错误，示例：'4月5日'、'4-5'、'4.5'等"
        
        month = int(match.group(2))
        day = int(match.group(4))
        
        if month < 1 or month > 12:
            return False, "月份错误，应为1-12"
        
        if day < 1 or day > 31:
            return False, "日期错误，应为1-31"
            
        return True, (month, day)
    
    def get_user_year(self):
        """让用户输入年份"""
        while True:
            year_input = input(f"{COLORS['INFO']}请输入当前年份（如2024）: ").strip()
            if year_input.lower() in ['q', 'quit', 'exit']:
                return None
            
            try:
                year = int(year_input)
                if 1900 <= year <= 2100:
                    return year
                else:
                    print(f"{COLORS['ERROR']}年份必须在1900-2100之间{COLORS['RESET']}")
            except ValueError:
                print(f"{COLORS['ERROR']}请输入有效的年份数字{COLORS['RESET']}")
    
    def get_cache_file(self, year):
        """生成缓存文件名"""
        return f'holidays_{year}.json'
    
    def download_holidays(self, year):
        """下载指定年份的节假日数据"""
        print(f"{COLORS['INFO']}正在下载{year}年节假日数据...{COLORS['RESET']}")
        
        # 检查本地缓存
        cache_file = self.get_cache_file(year)
        if os.path.exists(cache_file):
            with open(cache_file, 'r', encoding='utf-8') as f:
                try:
                    self.holidays[year] = json.load(f)
                    print(f"{COLORS['SUCCESS']}✓ 成功加载{year}年节假日缓存数据{COLORS['RESET']}")
                    return True
                except json.JSONDecodeError:
                    print(f"{COLORS['WARNING']}缓存文件损坏，重新下载{COLORS['RESET']}")
        
        # 使用免费的节假日API（中国）
        urls = [
            f"http://timor.tech/api/holiday/year/{year}",
            f"https://api.apihubs.cn/holiday/get?year={year}&size=366",
            f"http://api.goseek.cn/Tools/holiday?date={year}"
        ]
        
        for i, url in enumerate(urls):
            try:
                with tqdm(total=100, desc=f"尝试API {i+1}/3", 
                         bar_format='{desc}: {percentage:3.0f}%|{bar}| {elapsed}', leave=False) as pbar:
                    
                    response = requests.get(url, timeout=10)
                    pbar.update(30)
                    time.sleep(0.1)
                    
                    if response.status_code == 200:
                        pbar.update(50)
                        time.sleep(0.1)
                        
                        data = response.json()
                        pbar.update(20)
                        
                        holidays = self.parse_holiday_data(data, year)
                        if holidays:
                            self.holidays[year] = holidays
                            # 保存缓存
                            with open(cache_file, 'w', encoding='utf-8') as f:
                                json.dump(holidays, f, ensure_ascii=False, indent=2)
                            print(f"{COLORS['SUCCESS']}✓ 成功下载{year}年节假日数据（共{len(holidays)}个节假日）{COLORS['RESET']}")
                            return True
                        
            except Exception as e:
                print(f"{COLORS['WARNING']}API {i+1} 失败：{str(e)[:50]}...{COLORS['RESET']}")
                continue
        
        print(f"{COLORS['WARNING']}无法获取在线节假日数据，将使用基础周末判断{COLORS['RESET']}")
        self.holidays[year] = self.get_basic_holidays(year)
        return False
    
    def parse_holiday_data(self, data, year):
        """解析节假日数据"""
        holidays = {}
        
        try:
            # 适配不同API格式
            if isinstance(data, dict):
                if 'data' in data:
                    holiday_data = data['data']
                elif 'holiday' in data:
                    holiday_data = data['holiday']
                else:
                    holiday_data = data
                
                if isinstance(holiday_data, dict):
                    for date_str, info in holiday_data.items():
                        try:
                            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                            if date_obj.year == year:
                                if isinstance(info, dict):
                                    holidays[date_obj] = {
                                        'name': info.get('name', '节假日'),
                                        'is_holiday': info.get('holiday', True)
                                    }
                                else:
                                    holidays[date_obj] = {'name': str(info), 'is_holiday': True}
                        except:
                            continue
            # 特殊格式处理（如文件1的结构）
            elif isinstance(data, list) and len(data) > 0 and 'code' in data[0]:
                for item in data:
                    if item.get('code') == 0:
                        date_str = item.get('date', '')
                        if date_str.startswith(str(year)):
                            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
                            holidays[date_obj] = {
                                'name': item.get('name', '节假日'),
                                'is_holiday': True
                            }
            return holidays if len(holidays) > 5 else None  # 至少要有几个节假日才算有效
            
        except Exception:
            return None
    
    def get_basic_holidays(self, year):
        """获取基础节假日（当API不可用时）"""
        holidays = {}
        basic_holidays = [
            (1, 1, "元旦"),
            (5, 1, "劳动节"),
            (10, 1, "国庆节"),
            (10, 2, "国庆节"),
            (10, 3, "国庆节")
        ]
        
        for month, day, name in basic_holidays:
            try:
                date_obj = datetime.date(year, month, day)
                holidays[date_obj] = {'name': name, 'is_holiday': True}
            except:
                continue
                
        return holidays
    
    def get_weekday_info(self, year, month, day):
        """获取星期几信息"""
        try:
            date_obj = datetime.date(year, month, day)
            weekday = date_obj.weekday()  # 0=周一, 6=周日
            weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            weekday_colors = [Fore.BLUE, Fore.BLUE, Fore.BLUE, Fore.BLUE, Fore.BLUE, Fore.RED, Fore.RED]
            
            return {
                'date': date_obj,
                'weekday': weekday,
                'weekday_name': weekday_names[weekday],
                'weekday_color': weekday_colors[weekday],
                'is_weekend': weekday >= 5
            }
        except ValueError as e:
            return None
    
    def check_work_status(self, date_obj):
        """检查工作日状态"""
        year = date_obj.year
        
        # 确保有该年份的节假日数据
        if year not in self.holidays:
            self.download_holidays(year)
        
        holidays = self.holidays.get(year, {})
        
        # 检查是否是节假日
        if date_obj in holidays:
            holiday_info = holidays[date_obj]
            return {
                'status': '节假日',
                'color': Fore.RED,
                'detail': holiday_info['name']
            }
        
        # 检查是否是周末
        if date_obj.weekday() >= 5:
            return {
                'status': '周末',
                'color': Fore.YELLOW,
                'detail': '休息日'
            }
        
        # 工作日
        return {
            'status': '工作日',
            'color': Fore.GREEN,
            'detail': '正常工作日'
        }
    
    def format_date_info(self, year, month, day):
        """格式化日期信息"""
        try:
            weekday_info = self.get_weekday_info(year, month, day)
            if not weekday_info:
                return f"{COLORS['ERROR']}日期无效：{year}年{month}月{day}日{COLORS['RESET']}"
            
            date_obj = weekday_info['date']
            work_status = self.check_work_status(date_obj)
            
            # 格式化输出
            result = f"\n{COLORS['HEADER']}{'='*50}{COLORS['RESET']}"
            result += f"\n{COLORS['SUCCESS']}📅 具体日期查询结果{COLORS['RESET']}"
            result += f"\n{COLORS['HEADER']}{'='*50}{COLORS['RESET']}"
            result += f"\n{COLORS['SUCCESS']}日期：{date_obj.strftime('%Y年%m月%d日')}{COLORS['RESET']}"
            result += f"\n{weekday_info['weekday_color']}星期：{weekday_info['weekday_name']}{COLORS['RESET']}"
            result += f"\n{work_status['color']}状态：{work_status['status']} - {work_status['detail']}{COLORS['RESET']}"
            
            # 添加额外信息
            result += f"\n{COLORS['INFO']}天数：这是{year}年的第{date_obj.timetuple().tm_yday}天{COLORS['RESET']}"
            
            # 节假日详情
            if year in self.holidays and date_obj in self.holidays[year]:
                holiday = self.holidays[year][date_obj]
                result += f"\n{COLORS['ERROR']}节假日：{holiday['name']}{COLORS['RESET']}"
            
            result += f"\n{COLORS['HEADER']}{'='*50}\n{COLORS['RESET']}"
            
            return result
        except Exception as e:
            return f"{COLORS['ERROR']}格式化错误：{str(e)}{COLORS['RESET']}"
    
    def run(self):
        """运行具体日期查询功能"""
        print(f"{COLORS['HEADER']}{'='*60}{COLORS['RESET']}")
        print(f"{COLORS['SUCCESS']}🔍 具体日期查询工具{COLORS['RESET']}")
        print(f"{COLORS['HEADER']}{'='*60}{COLORS['RESET']}")
        print(f"{COLORS['INFO']}功能：查询指定日期是星期几，判断是否为工作日/周末/节假日{COLORS['RESET']}")
        print(f"{COLORS['INFO']}支持格式：4月5日、4-5、4.5、4&5、4 5等{COLORS['RESET']}")
        print(f"{COLORS['WARNING']}退出：输入 'q'、'quit' 或 'exit' 退出程序\n{COLORS['RESET']}")
        
        # 获取当前年份
        current_year = self.get_system_time()
        if current_year is None:
            current_year = self.get_user_year()
            if current_year is None:
                print(f"{COLORS['WARNING']}程序已退出{COLORS['RESET']}")
                return
        
        self.current_year = current_year
        
        # 预下载当前年份的节假日数据
        self.download_holidays(current_year)
        
        print(f"\n{COLORS['SUCCESS']}准备完成！当前基准年份：{current_year}{COLORS['RESET']}")
        
        while True:
            print(f"\n{COLORS['INFO']}{'─'*50}{COLORS['RESET']}")
            
            # 获取年份
            year_input = input(f"{COLORS['INFO']}请输入查询年份（直接回车使用{current_year}）: ").strip()
            if year_input.lower() in ['q', 'quit', 'exit']:
                break
            
            if year_input:
                try:
                    query_year = int(year_input)
                    if not (1900 <= query_year <= 2100):
                        print(f"{COLORS['ERROR']}年份必须在1900-2100之间{COLORS['RESET']}")
                        continue
                except ValueError:
                    print(f"{COLORS['ERROR']}请输入有效的年份{COLORS['RESET']}")
                    continue
            else:
                query_year = current_year
            
            # 获取月日
            date_input = input(f"{COLORS['INFO']}请输入月份和日期（如：4月5日、4-5、4.5等）: ").strip()
            if date_input.lower() in ['q', 'quit', 'exit']:
                break
            
            # 验证输入
            valid, result = self.validate_date_input(date_input)
            if not valid:
                print(f"{COLORS['ERROR']}输入错误：{result}{COLORS['RESET']}")
                continue
            
            month, day = result
            
            # 检查并下载该年份的节假日数据
            if query_year not in self.holidays:
                self.download_holidays(query_year)
            
            # 显示结果
            info = self.format_date_info(query_year, month, day)
            print(info)
            
            # 继续选项
            continue_input = input(f"{COLORS['INFO']}按Enter继续查询，或输入'q'退出到主菜单: ").strip()
            if continue_input.lower() in ['q', 'quit', 'exit']:
                break


class WeekdayPossibilityAnalyzer:
    """日期星期几可能性分析工具"""
    
    @staticmethod
    def validate_date_input(text):
        """验证日期输入格式"""
        pattern = re.compile(r'^([^\d]*)(\d{1,2})([^\d]+)(\d{1,2})([^\d]*)$')
        match = pattern.match(text.strip())
        if not match:
            return False, "输入格式错误，示例：'4月5日'、'4-5'、'4.5'等"
        
        month = int(match.group(2))
        day = int(match.group(4))
        
        if month < 1 or month > 12:
            return False, "月份错误，应为1-12"
        
        if day < 1 or day > 31:
            return False, "日期错误，应为1-31"
            
        return True, (month, day)
    
    def calculate_weekday_possibilities(self, month, day, year_range=(2020, 2030)):
        """计算某月某日在指定年份范围内可能的星期几情况"""
        weekday_stats = {i: [] for i in range(7)}  # 0-6对应周一到周日
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        
        for year in range(year_range[0], year_range[1] + 1):
            try:
                date_obj = datetime.date(year, month, day)
                weekday = date_obj.weekday()
                weekday_stats[weekday].append(year)
            except ValueError:
                # 处理2月29日等无效日期
                continue
        
        weekend_years = []
        workday_years = []
        
        # 周末：周六(5)和周日(6)
        weekend_years.extend(weekday_stats[5])  # 周六
        weekend_years.extend(weekday_stats[6])  # 周日
        
        # 工作日：周一到周五(0-4)
        for i in range(5):
            workday_years.extend(weekday_stats[i])
        
        weekend_years.sort()
        workday_years.sort()
        
        return {
            'weekday_stats': weekday_stats,
            'weekday_names': weekday_names,
            'weekend_years': weekend_years,
            'workday_years': workday_years,
            'year_range': year_range
        }
    
    def format_possibility_analysis(self, month, day, year_range=(2020, 2030)):
        """格式化可能性分析结果"""
        try:
            analysis = self.calculate_weekday_possibilities(month, day, year_range)
            
            result = f"\n{COLORS['HEADER']}{'='*65}{COLORS['RESET']}"
            result += f"\n{COLORS['SUCCESS']}📊 {month}月{day}日 星期几可能性分析 ({year_range[0]}-{year_range[1]}){COLORS['RESET']}"
            result += f"\n{COLORS['HEADER']}{'='*65}{COLORS['RESET']}"
            
            # 详细的星期分布
            result += f"\n{COLORS['INFO']}📈 各星期几出现情况：{COLORS['RESET']}"
            for weekday, years in analysis['weekday_stats'].items():
                if years:
                    weekday_name = analysis['weekday_names'][weekday]
                    color = COLORS['WARNING'] if weekday >= 5 else COLORS['SUCCESS']
                    years_str = ', '.join(map(str, years[:8]))  # 显示前8个年份
                    if len(years) > 8:
                        years_str += f" 等{len(years)}年"
                    result += f"\n  {color}{weekday_name:<3}：{years_str}{COLORS['RESET']}"
            
            # 周末可能性
            result += f"\n\n{COLORS['WARNING']}🏖️  周末可能性分析：{COLORS['RESET']}"
            if analysis['weekend_years']:
                result += f"\n  {COLORS['ERROR']}✓ 可能是周末：{len(analysis['weekend_years'])}个年份{COLORS['RESET']}"
                weekend_display = ', '.join(map(str, analysis['weekend_years'][:15]))
                if len(analysis['weekend_years']) > 15:
                    weekend_display += f" 等{len(analysis['weekend_years'])}年"
                result += f"\n  {COLORS['WARNING']}年份列表：{weekend_display}{COLORS['RESET']}"
            else:
                result += f"\n  {COLORS['INFO']}✗ 在{year_range[0]}-{year_range[1]}年间不会是周末{COLORS['RESET']}"
            
            # 工作日可能性
            result += f"\n\n{COLORS['INFO']}💼 工作日可能性分析：{COLORS['RESET']}"
            if analysis['workday_years']:
                result += f"\n  {COLORS['SUCCESS']}✓ 可能是工作日：{len(analysis['workday_years'])}个年份{COLORS['RESET']}"
                workday_display = ', '.join(map(str, analysis['workday_years'][:15]))
                if len(analysis['workday_years']) > 15:
                    workday_display += f" 等{len(analysis['workday_years'])}年"
                result += f"\n  {COLORS['SUCCESS']}年份列表：{workday_display}{COLORS['RESET']}"
            else:
                result += f"\n  {COLORS['INFO']}✗ 在{year_range[0]}-{year_range[1]}年间不会是工作日{COLORS['RESET']}"
            
            # 统计信息
            total_years = year_range[1] - year_range[0] + 1
            valid_years = len(analysis['weekend_years']) + len(analysis['workday_years'])
            result += f"\n\n{COLORS['INFO']}📋 统计摘要：{COLORS['RESET']}"
            result += f"\n  分析年份范围：{total_years}年"
            result += f"\n  有效年份数量：{valid_years}年"
            if valid_years > 0:
                weekend_pct = len(analysis['weekend_years']) / valid_years * 100
                workday_pct = len(analysis['workday_years']) / valid_years * 100
                result += f"\n  周末概率：{weekend_pct:.1f}%"
                result += f"\n  工作日概率：{workday_pct:.1f}%"
            
            # 特殊情况说明
            if month == 2 and day == 29:
                result += f"\n{COLORS['WARNING']}📝 注意：2月29日仅在闰年存在（如{random.choice(analysis['workday_years'])}年）{COLORS['RESET']}"
            
            result += f"\n{COLORS['HEADER']}{'='*65}\n{COLORS['RESET']}"
            return result
            
        except Exception as e:
            return f"{COLORS['ERROR']}分析失败：{str(e)}{COLORS['RESET']}"
    
    def run(self):
        """运行可能性分析功能"""
        print(f"{COLORS['HEADER']}{'='*60}{COLORS['RESET']}")
        print(f"{COLORS['SUCCESS']}📊 日期星期几可能性分析工具{COLORS['RESET']}")
        print(f"{COLORS['HEADER']}{'='*60}{COLORS['RESET']}")
        print(f"{COLORS['INFO']}功能：分析某月某日在不同年份中可能是周末/工作日的情况{COLORS['RESET']}")
        print(f"{COLORS['INFO']}支持格式：4月5日、4-5、4.5、4&5、4 5等{COLORS['RESET']}")
        print(f"{COLORS['WARNING']}退出：输入 'q'、'quit' 或 'exit' 退出程序\n{COLORS['RESET']}")
        
        while True:
            print(f"\n{COLORS['INFO']}{'─'*50}{COLORS['RESET']}")
            
            # 获取要分析的日期
            date_input = input(f"{COLORS['INFO']}请输入要分析的月份和日期（如：4月5日、4-5、4.5等）: ").strip()
            if date_input.lower() in ['q', 'quit', 'exit']:
                break
            
            # 验证输入
            valid, result = self.validate_date_input(date_input)
            if not valid:
                print(f"{COLORS['ERROR']}输入错误：{result}{COLORS['RESET']}")
                continue
            
            month, day = result
            
            # 获取年份范围
            range_input = input(f"{COLORS['INFO']}请输入分析年份范围（如：2020-2030，直接回车使用2020-2030）: ").strip()
            if range_input.lower() in ['q', 'quit', 'exit']:
                break
            
            year_range = (2020, 2030)  # 默认范围
            if range_input:
                try:
                    if '-' in range_input:
                        start_year, end_year = map(int, range_input.split('-'))
                        if 1900 <= start_year <= end_year <= 2100:
                            if end_year - start_year > 200:
                                print(f"{COLORS['WARNING']}年份范围过大，建议不超过200年，已自动调整为{start_year}-{start_year+200}{COLORS['RESET']}")
                                year_range = (start_year, start_year + 200)
                            else:
                                year_range = (start_year, end_year)
                        else:
                            print(f"{COLORS['ERROR']}年份范围必须在1900-2100之间，且起始年份不能大于结束年份{COLORS['RESET']}")
                            continue
                    else:
                        print(f"{COLORS['ERROR']}年份范围格式错误，应为：起始年份-结束年份{COLORS['RESET']}")
                        continue
                except ValueError:
                    print(f"{COLORS['ERROR']}年份范围格式错误，请输入有效数字{COLORS['RESET']}")
                    continue
            
            # 显示分析进度
            print(f"{COLORS['INFO']}正在分析{month}月{day}日在{year_range[0]}-{year_range[1]}年的情况...{COLORS['RESET']}")
            with tqdm(total=100, desc="分析进度", bar_format='{desc}: {percentage:3.0f}%|{bar}| {elapsed}', leave=False) as pbar:
                pbar.update(30)
                time.sleep(0.1)
                
                # 显示分析结果
                analysis = self.format_possibility_analysis(month, day, year_range)
                pbar.update(70)
                time.sleep(0.1)
            
            print(analysis)
            
            # 继续选项
            continue_input = input(f"{COLORS['INFO']}按Enter继续分析，或输入'q'退出到主菜单: ").strip()
            if continue_input.lower() in ['q', 'quit', 'exit']:
                break


def show_main_menu():
    """显示主菜单"""
    print(f"{COLORS['HEADER']}{'='*70}{COLORS['RESET']}")
    print(f"{COLORS['SUCCESS']}               🗓️  日期查询工具套件{COLORS['RESET']}")
    print(f"{COLORS['HEADER']}{'='*70}{COLORS['RESET']}")
    print(f"{COLORS['INFO']}请选择您需要的功能：{COLORS['RESET']}")
    print(f"{COLORS['SUCCESS']}  1️⃣  查询具体日期{COLORS['RESET']}")
    print(f"{COLORS['INFO']}       查询指定年月日是星期几，判断工作日/周末/节假日状态{COLORS['RESET']}")
    print(f"{COLORS['WARNING']}  2️⃣  分析日期可能性{COLORS['RESET']}")
    print(f"{COLORS['INFO']}       分析某月某日在不同年份中可能是周末/工作日的情况{COLORS['RESET']}")
    print(f"{COLORS['ERROR']}  0️⃣  退出程序{COLORS['RESET']}")
    print(f"{COLORS['HEADER']}{'='*70}{COLORS['RESET']}")

def main():
    """主程序入口"""
    try:
        while True:
            show_main_menu()
            
            choice = input(f"{COLORS['INFO']}请输入功能编号（0-2）: ").strip()
            
            if choice == '0' or choice.lower() in ['q', 'quit', 'exit']:
                print(f"{COLORS['HEADER']}感谢使用日期查询工具！再见 👋{COLORS['RESET']}")
                break
            elif choice == '1':
                # 具体日期查询
                date_query = DateQueryTool()
                date_query.run()
            elif choice == '2':
                # 可能性分析
                analyzer = WeekdayPossibilityAnalyzer()
                analyzer.run()
            else:
                print(f"{COLORS['ERROR']}无效选择，请输入 0、1 或 2{COLORS['RESET']}")
                input(f"{COLORS['WARNING']}按Enter键继续...{COLORS['RESET']}")
                continue
            
    except KeyboardInterrupt:
        print(f"\n{COLORS['WARNING']}程序已中断{COLORS['RESET']}")
    except Exception as e:
        print(f"\n{COLORS['ERROR']}程序出现错误：{e}{COLORS['RESET']}")

if __name__ == '__main__':
    main()