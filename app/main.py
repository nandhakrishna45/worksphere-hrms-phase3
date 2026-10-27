from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4
import csv
import io

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db, SessionLocal
from app.models import User, Employee, Shift, EmployeeShift, Attendance, Leave, Document, Performance, Notification
from app.schemas import LoginIn, TokenOut, EmployeeIn, EmployeeOut, ShiftIn, ShiftOut, AssignmentIn, CheckInOut, LeaveIn, LeaveReview, PerformanceIn, NotificationOut
from app.security import hash_password, verify_password, create_access_token, get_current_user, require_roles

def bootstrap():
    Base.metadata.create_all(bind=engine)
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        db = SessionLocal()
        try:
            exists = db.query(User).filter(User.email == settings.bootstrap_admin_email).first()
            if not exists:
                db.add(User(email=settings.bootstrap_admin_email, hashed_password=hash_password(settings.bootstrap_admin_password), role="admin"))
                db.commit()
        finally:
            db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    yield

app = FastAPI(title=settings.app_name, version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.allowed_origins.split(",") if x.strip()], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def notify(db: Session, title: str, message: str, category: str, employee_id: int | None = None):
    db.add(Notification(title=title, message=message, category=category, employee_id=employee_id))

def employee_or_404(db: Session, employee_id: int):
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "Employee not found")
    return employee

def employee_shift_for_day(db: Session, employee_id: int, day: date):
    return (db.query(EmployeeShift).filter(EmployeeShift.employee_id == employee_id, EmployeeShift.effective_from <= day)
            .order_by(EmployeeShift.effective_from.desc()).first())

@app.get("/")
def root():
    return {"app": settings.app_name, "phase": 3, "docs": "/docs"}

@app.post("/auth/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.hashed_password) or not user.is_active:
        raise HTTPException(401, "Incorrect email or password")
    return TokenOut(access_token=create_access_token(user))

@app.post("/employees", response_model=EmployeeOut)
def create_employee(data: EmployeeIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr"))):
    employee = Employee(**data.model_dump())
    db.add(employee); db.commit(); db.refresh(employee)
    return employee

@app.get("/employees", response_model=list[EmployeeOut])
def search_employees(q: str | None = None, department: str | None = None, role: str | None = None,
                     status: str | None = None, attendance: str | None = None, min_rating: float | None = Query(default=None, ge=1, le=5),
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(Employee)
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(Employee.full_name.ilike(term))
    if department: query = query.filter(Employee.department.ilike(f"%{department}%"))
    if role: query = query.filter(Employee.job_role.ilike(f"%{role}%"))
    if status: query = query.filter(Employee.status == status)
    if attendance:
        today = date.today()
        ids = db.query(Attendance.employee_id).filter(Attendance.attendance_date == today, Attendance.status == attendance)
        query = query.filter(Employee.id.in_(ids))
    if min_rating is not None:
        ids = db.query(Performance.employee_id).group_by(Performance.employee_id).having(func.avg(Performance.rating) >= min_rating)
        query = query.filter(Employee.id.in_(ids))
    return query.order_by(Employee.full_name).limit(500).all()

# Module 14 — shifts and attendance
@app.post("/shifts", response_model=ShiftOut)
def create_shift(data: ShiftIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr"))):
    if db.query(Shift).filter(Shift.name == data.name).first(): raise HTTPException(409, "Shift name already exists")
    item = Shift(**data.model_dump()); db.add(item); db.commit(); db.refresh(item); return item

@app.get("/shifts", response_model=list[ShiftOut])
def list_shifts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Shift).order_by(Shift.start_time).all()

@app.post("/employee-shifts")
def assign_shift(data: AssignmentIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr"))):
    employee_or_404(db, data.employee_id)
    if not db.get(Shift, data.shift_id): raise HTTPException(404, "Shift not found")
    existing = db.query(EmployeeShift).filter(EmployeeShift.employee_id == data.employee_id, EmployeeShift.effective_from == data.effective_from).first()
    if existing: existing.shift_id = data.shift_id
    else: db.add(EmployeeShift(**data.model_dump()))
    db.commit()
    return {"message": "Shift assigned"}

@app.get("/employee-shifts")
def shift_schedule(day: date = Query(default_factory=date.today), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(EmployeeShift).filter(EmployeeShift.effective_from <= day).all()
    latest = {}
    for row in rows:
        if row.employee_id not in latest or row.effective_from > latest[row.employee_id].effective_from: latest[row.employee_id] = row
    return [{"employee_id": r.employee_id, "employee_name": r.employee.full_name, "shift_id": r.shift_id, "shift_name": r.shift.name, "start_time": r.shift.start_time, "end_time": r.shift.end_time, "effective_from": r.effective_from} for r in latest.values()]

@app.post("/attendance/check-in")
def check_in(data: CheckInOut, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == "employee" and user.employee_id != data.employee_id: raise HTTPException(403, "Cannot check in for another employee")
    employee_or_404(db, data.employee_id)
    today = date.today()
    row = db.query(Attendance).filter_by(employee_id=data.employee_id, attendance_date=today).first()
    if row and row.check_in: raise HTTPException(409, "Check-in already recorded")
    now = datetime.now()
    assignment = employee_shift_for_day(db, data.employee_id, today)
    late = 0
    if assignment:
        scheduled = datetime.combine(today, assignment.shift.start_time)
        if assignment.shift.end_time < assignment.shift.start_time and now.time() < assignment.shift.end_time:
            scheduled -= timedelta(days=1)
        late = max(0, int((now - scheduled).total_seconds() // 60))
    if not row:
        row = Attendance(employee_id=data.employee_id, attendance_date=today)
        db.add(row)
    row.check_in = now; row.late_minutes = late; row.status = "late" if late > 0 else "present"
    if late > 0: notify(db, "Late attendance", f"Employee {data.employee_id} checked in {late} minutes late.", "attendance", data.employee_id)
    db.commit()
    return {"message": "Check-in recorded", "check_in": now, "late_minutes": late, "status": row.status}

@app.post("/attendance/check-out")
def check_out(data: CheckInOut, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == "employee" and user.employee_id != data.employee_id: raise HTTPException(403, "Cannot check out another employee")
    row = db.query(Attendance).filter_by(employee_id=data.employee_id, attendance_date=date.today()).first()
    if not row or not row.check_in: raise HTTPException(400, "Check-in must be recorded first")
    if row.check_out: raise HTTPException(409, "Check-out already recorded")
    now = datetime.now(); row.check_out = now
    assignment = employee_shift_for_day(db, data.employee_id, date.today())
    if assignment:
        scheduled = datetime.combine(date.today(), assignment.shift.end_time)
        if assignment.shift.end_time < assignment.shift.start_time and now.time() > assignment.shift.start_time:
            scheduled += timedelta(days=1)
        row.early_checkout_minutes = max(0, int((scheduled - now).total_seconds() // 60))
    db.commit()
    return {"message": "Check-out recorded", "check_out": now, "early_checkout_minutes": row.early_checkout_minutes}

@app.get("/attendance")
def attendance_report(start_date: date | None = None, end_date: date | None = None, department: str | None = None,
                      db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr", "manager"))):
    q = db.query(Attendance, Employee).join(Employee, Attendance.employee_id == Employee.id)
    if start_date: q = q.filter(Attendance.attendance_date >= start_date)
    if end_date: q = q.filter(Attendance.attendance_date <= end_date)
    if department: q = q.filter(Employee.department == department)
    return [{"employee_id": e.id, "employee_name": e.full_name, "department": e.department, "date": a.attendance_date, "status": a.status, "check_in": a.check_in, "check_out": a.check_out, "late_minutes": a.late_minutes, "early_checkout_minutes": a.early_checkout_minutes} for a,e in q.order_by(Attendance.attendance_date.desc()).limit(2000).all()]

# Module 15 — leave calendar
@app.post("/leaves")
def request_leave(data: LeaveIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == "employee" and user.employee_id != data.employee_id: raise HTTPException(403, "Cannot request leave for another employee")
    employee_or_404(db, data.employee_id)
    conflict = db.query(Leave).filter(Leave.employee_id == data.employee_id, Leave.status.in_(["pending","approved"]),
        Leave.start_date <= data.end_date, Leave.end_date >= data.start_date).first()
    if conflict: raise HTTPException(409, "Overlapping leave request exists")
    item = Leave(**data.model_dump()); db.add(item)
    notify(db, "Leave request submitted", f"Employee {data.employee_id} requested leave from {data.start_date} to {data.end_date}.", "leave", data.employee_id)
    db.commit(); db.refresh(item); return {"id": item.id, "status": item.status}

@app.get("/leaves/calendar")
def leave_calendar(start_date: date, end_date: date, department: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if end_date < start_date: raise HTTPException(422, "end_date must be on or after start_date")
    q = db.query(Leave, Employee).join(Employee, Leave.employee_id == Employee.id).filter(
        Leave.status == "approved", Leave.start_date <= end_date, Leave.end_date >= start_date)
    if department: q = q.filter(Employee.department == department)
    return [{"leave_id": l.id, "employee_id": e.id, "employee_name": e.full_name, "department": e.department, "start_date": l.start_date, "end_date": l.end_date, "leave_type": l.leave_type} for l,e in q.all()]

@app.get("/leaves/team-availability")
def team_availability(day: date = Query(default_factory=date.today), department: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Employee).filter(Employee.status == "active")
    if department: q = q.filter(Employee.department == department)
    employees = q.order_by(Employee.full_name).all()
    on_leave = {l.employee_id for l in db.query(Leave).filter(Leave.status == "approved", Leave.start_date <= day, Leave.end_date >= day).all()}
    return [{"employee_id": e.id, "name": e.full_name, "department": e.department, "availability": "on_leave" if e.id in on_leave else "available"} for e in employees]

@app.post("/leaves/{leave_id}/review")
def review_leave(leave_id: int, data: LeaveReview, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr", "manager"))):
    if data.status not in {"approved", "rejected"}: raise HTTPException(422, "status must be approved or rejected")
    item = db.get(Leave, leave_id)
    if not item: raise HTTPException(404, "Leave request not found")
    item.status = data.status; item.reviewed_by = user.id
    notify(db, f"Leave {data.status}", f"Your leave request #{leave_id} was {data.status}.", "leave", item.employee_id)
    db.commit(); return {"message": f"Leave {data.status}"}

# Module 16 — secure document handling
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}
@app.post("/documents")
async def upload_document(employee_id: int, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == "employee" and user.employee_id != employee_id: raise HTTPException(403, "Cannot upload for another employee")
    employee_or_404(db, employee_id)
    original = Path(file.filename or "document").name
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS: raise HTTPException(415, "File type not allowed")
    contents = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if not contents: raise HTTPException(400, "Empty file")
    if len(contents) > settings.max_upload_mb * 1024 * 1024: raise HTTPException(413, "File too large")
    stored = f"{uuid4().hex}{ext}"
    path = Path(settings.upload_dir) / stored
    path.write_bytes(contents)
    record = Document(employee_id=employee_id, file_name=original, stored_name=stored, content_type=file.content_type or "application/octet-stream")
    db.add(record); db.commit(); db.refresh(record)
    return {"id": record.id, "employee_id": employee_id, "file_name": record.file_name, "uploaded_at": record.uploaded_at}

@app.get("/documents")
def list_documents(employee_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Document)
    if user.role == "employee":
        if not user.employee_id: raise HTTPException(403, "Employee account is not linked")
        q = q.filter(Document.employee_id == user.employee_id)
    elif employee_id:
        q = q.filter(Document.employee_id == employee_id)
    return [{"id": d.id, "employee_id": d.employee_id, "file_name": d.file_name, "uploaded_at": d.uploaded_at} for d in q.order_by(Document.uploaded_at.desc()).all()]

@app.get("/documents/{document_id}/download")
def download_document(document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    d = db.get(Document, document_id)
    if not d: raise HTTPException(404, "Document not found")
    if user.role == "employee" and user.employee_id != d.employee_id: raise HTTPException(403, "Forbidden")
    path = Path(settings.upload_dir) / d.stored_name
    if not path.is_file(): raise HTTPException(404, "Stored file missing")
    return FileResponse(path, filename=d.file_name, media_type=d.content_type)

# Module 17 — performance reviews
@app.post("/performance")
def create_performance(data: PerformanceIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr", "manager"))):
    employee_or_404(db, data.employee_id)
    item = Performance(**data.model_dump()); db.add(item)
    notify(db, "Performance review added", f"A performance review was recorded for employee {data.employee_id}.", "performance", data.employee_id)
    db.commit(); db.refresh(item)
    return {"id": item.id, "employee_id": item.employee_id, "review_period": item.review_period, "rating": item.rating, "feedback": item.feedback, "created_at": item.created_at}

@app.get("/performance")
def performance_history(employee_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Performance)
    if user.role == "employee":
        if not user.employee_id: raise HTTPException(403, "Employee account is not linked")
        q = q.filter(Performance.employee_id == user.employee_id)
    elif employee_id: q = q.filter(Performance.employee_id == employee_id)
    return [{"id": p.id, "employee_id": p.employee_id, "review_period": p.review_period, "rating": p.rating, "feedback": p.feedback, "created_at": p.created_at} for p in q.order_by(Performance.created_at.desc()).all()]

# Module 18 — analytics and CSV export
@app.get("/analytics/summary")
def analytics_summary(start_date: date | None = None, end_date: date | None = None, department: str | None = None,
                      db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr", "manager"))):
    aq = db.query(Attendance).join(Employee, Attendance.employee_id == Employee.id)
    lq = db.query(Leave).join(Employee, Leave.employee_id == Employee.id)
    pq = db.query(Performance).join(Employee, Performance.employee_id == Employee.id)
    if start_date:
        aq = aq.filter(Attendance.attendance_date >= start_date); lq = lq.filter(Leave.end_date >= start_date); pq = pq.filter(Performance.created_at >= datetime.combine(start_date, time.min))
    if end_date:
        aq = aq.filter(Attendance.attendance_date <= end_date); lq = lq.filter(Leave.start_date <= end_date); pq = pq.filter(Performance.created_at <= datetime.combine(end_date, time.max))
    if department:
        aq = aq.join(Employee, Attendance.employee_id == Employee.id) if False else aq
        aq = aq.filter(Employee.department == department); lq = lq.filter(Employee.department == department); pq = pq.filter(Employee.department == department)
    attendance_count = aq.count()
    late_count = aq.filter(Attendance.late_minutes > 0).count()
    leave_counts = {status: count for status, count in lq.with_entities(Leave.status, func.count(Leave.id)).group_by(Leave.status).all()}
    avg_rating = pq.with_entities(func.avg(Performance.rating)).scalar()
    dept_rows = db.query(Employee.department, func.count(Attendance.id), func.sum(Attendance.late_minutes > 0)).join(Attendance, Attendance.employee_id == Employee.id).group_by(Employee.department).all()
    return {"attendance_records": attendance_count, "late_records": late_count, "late_rate_percent": round(late_count * 100 / attendance_count, 2) if attendance_count else 0,
            "leave_requests_by_status": leave_counts, "average_performance_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
            "department_attendance": [{"department": d, "attendance_records": c, "late_records": int(l or 0)} for d,c,l in dept_rows]}

@app.get("/analytics/attendance.csv")
def attendance_csv(start_date: date | None = None, end_date: date | None = None, db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "hr", "manager"))):
    q = db.query(Attendance, Employee).join(Employee, Attendance.employee_id == Employee.id)
    if start_date: q = q.filter(Attendance.attendance_date >= start_date)
    if end_date: q = q.filter(Attendance.attendance_date <= end_date)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["employee_id","employee_name","department","date","status","check_in","check_out","late_minutes","early_checkout_minutes"])
    for a,e in q.order_by(Attendance.attendance_date.desc()).all():
        writer.writerow([e.id,e.full_name,e.department,a.attendance_date,a.status,a.check_in,a.check_out,a.late_minutes,a.early_checkout_minutes])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=attendance-report.csv"})

# Module 19 — notifications
@app.get("/notifications", response_model=list[NotificationOut])
def list_notifications(unread_only: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Notification)
    if user.role == "employee":
        q = q.filter(or_(Notification.employee_id == user.employee_id, Notification.employee_id.is_(None)))
    if unread_only: q = q.filter(Notification.is_read.is_(False))
    return q.order_by(Notification.created_at.desc()).limit(200).all()

@app.patch("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    n = db.get(Notification, notification_id)
    if not n: raise HTTPException(404, "Notification not found")
    if user.role == "employee" and n.employee_id not in (None, user.employee_id): raise HTTPException(403, "Forbidden")
    n.is_read = True; db.commit(); return {"message":"Notification marked as read"}

@app.get("/health")
def health():
    return {"status":"ok"}
