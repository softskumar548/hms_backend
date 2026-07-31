
from pydantic import BaseModel


class OperationalDashboardOut(BaseModel):
    appointments_count: int
    arrivals_count: int
    avg_wait_minutes: float
    revenue_collected: float
    queue_length: int
    today_visits: int = 0
    today_revenue: float = 0.0
    no_shows: int = 0


class VisitsReportItem(BaseModel):
    practitioner_id: str
    service_id: str
    visits_count: int


class RevenueReportItem(BaseModel):
    category: str
    payer_type: str  # 'patient', 'aarogyasri', 'pmjay', 'private_insurer'
    amount: float


class DiagnosesReportItem(BaseModel):
    icd10_code: str
    display: str
    patient_count: int


class ARAgingReportItem(BaseModel):
    bucket: str  # '0-30', '31-60', '61-90', '90+'
    outstanding_amount: float
