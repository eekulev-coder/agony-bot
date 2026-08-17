import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger("Scheduler")


class WarScheduler:
    """Планировщик напоминаний о забивах"""
    
    def __init__(self):
        self.tasks = {}  # {war_id: [task1, task2, ...]}
    
    def schedule_reminders(
        self,
        war_id: str,
        war_time_str: str,
        remind_before: list,
        callback: Callable
    ):
        """
        Планирует напоминания.
        
        war_id: уникальный ID забива (например 'war_2210_20250116')
        war_time_str: время забива в формате 'HH:MM'
        remind_before: список минут до забива [15, 5, 1]
        callback: async функция(minutes_left) для оповещения
        """
        # Парсим время
        try:
            hour, minute = map(int, war_time_str.split(":"))
        except ValueError:
            logger.error(f"❌ Некорректное время: {war_time_str}")
            return
        
        now = datetime.now()
        war_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # Если время уже прошло — считаем что это на завтра
        if war_time < now:
            war_time += timedelta(days=1)
        
        self.tasks[war_id] = []
        
        for mins in remind_before:
            remind_time = war_time - timedelta(minutes=mins)
            delay = (remind_time - now).total_seconds()
            
            if delay <= 0:
                logger.info(f"⏭️ Напоминание за {mins} мин уже прошло")
                continue
            
            task = asyncio.create_task(
                self._delayed_reminder(delay, mins, callback)
            )
            self.tasks[war_id].append(task)
            logger.info(f"⏰ Запланировано напоминание за {mins} мин (через {int(delay)}с)")
    
    async def _delayed_reminder(self, delay: float, minutes_left: int, callback: Callable):
        try:
            await asyncio.sleep(delay)
            await callback(minutes_left)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"❌ Ошибка напоминания: {e}")
    
    def cancel(self, war_id: str):
        """Отменить все напоминания для конкретного забива"""
        if war_id in self.tasks:
            for task in self.tasks[war_id]:
                task.cancel()
            del self.tasks[war_id]
