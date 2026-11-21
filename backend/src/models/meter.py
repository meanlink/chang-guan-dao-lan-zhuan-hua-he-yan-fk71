from sqlalchemy import String, Integer, Double, ForeignKey, Boolean, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MeterDevice(Base):
    """计数器设备：登记所在展区、导览方案与换机代次。累计读数跨日不清零。"""
    __tablename__ = 'meter_devices'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    zone: Mapped[str] = mapped_column(String(80))
    arm: Mapped[str] = mapped_column(String(40))
    # 换机代次：同一设备每次换新机递增；代次切换以首日读数为新基线
    generation: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str] = mapped_column(String(300), default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Double)


class MeterReading(Base):
    """累计读数（进区累计/完成累计）。同一设备代次同一自然日唯一；更正保留版本号。"""
    __tablename__ = 'meter_readings'
    __table_args__ = (
        UniqueConstraint('device_id', 'generation', 'reading_date', name='uq_reading_device_generation_date'),
        Index('ix_reading_device_date', 'device_id', 'reading_date'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey('meter_devices.id'))
    generation: Mapped[int] = mapped_column(Integer)
    reading_date: Mapped[str] = mapped_column(String(10))
    entry_total: Mapped[int] = mapped_column(Integer)
    completed_total: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default='confirmed')
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class MeterCorrection(Base):
    """读数更正留痕：旧值、新值、触发的重算批次与原因。"""
    __tablename__ = 'meter_corrections'
    id: Mapped[int] = mapped_column(primary_key=True)
    reading_id: Mapped[int] = mapped_column(ForeignKey('meter_readings.id'))
    device_id: Mapped[int] = mapped_column(ForeignKey('meter_devices.id'))
    reading_date: Mapped[str] = mapped_column(String(10))
    old_entry: Mapped[int] = mapped_column(Integer)
    old_completed: Mapped[int] = mapped_column(Integer)
    new_entry: Mapped[int] = mapped_column(Integer)
    new_completed: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(300), default='')
    batch_id: Mapped[int | None] = mapped_column(ForeignKey('settlement_batches.id'), nullable=True)
    actor: Mapped[str] = mapped_column(String(150))
    created_at: Mapped[float] = mapped_column(Double)


class SettlementBatch(Base):
    """结算批次：一次初始结算或一次上游更正触发的整组重算。"""
    __tablename__ = 'settlement_batches'
    __table_args__ = (Index('ix_batch_device_generation', 'device_id', 'generation'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey('meter_devices.id'))
    generation: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default='recalculating')
    # 执行所依据的读数版本快照；执行时与当前版本不符则拒绝写回（过期执行）
    input_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    affected_dates: Mapped[list] = mapped_column(JSON, default=list)
    invalid_reason: Mapped[str] = mapped_column(String(500), default='')
    progress: Mapped[str] = mapped_column(String(30), default='pending')
    total_dates: Mapped[int] = mapped_column(Integer, default=0)
    processed_dates: Mapped[int] = mapped_column(Integer, default=0)
    trigger: Mapped[str] = mapped_column(String(30), default='correction')
    created_by: Mapped[str] = mapped_column(String(150), default='system')
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class SettlementLine(Base):
    """日结算行：按相邻日读数差生成的进区/完成人数，或缺日/断档/隔离/失效记录。"""
    __tablename__ = 'settlement_lines'
    __table_args__ = (Index('ix_line_batch', 'batch_id'), Index('ix_line_device_date', 'device_id', 'reading_date'))
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey('settlement_batches.id'))
    device_id: Mapped[int] = mapped_column(ForeignKey('meter_devices.id'))
    generation: Mapped[int] = mapped_column(Integer)
    reading_date: Mapped[str] = mapped_column(String(10))
    prev_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='active')
    entry_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(300), default='')
    created_at: Mapped[float] = mapped_column(Double)
