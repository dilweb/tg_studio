from aiogram.fsm.state import State, StatesGroup


class BookingFlow(StatesGroup):
    choosing_master   = State()
    choosing_service  = State()
    choosing_duration = State()
    choosing_date     = State()
    choosing_time     = State()
    confirming        = State()
