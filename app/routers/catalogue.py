from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import DbSession
from app.exceptions import AppError, ErrorResponse
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest
from app.pagination import Page, PageQuery, paginate
from app.security import get_current_user

router = APIRouter(tags=["catalogue"])
catalogue_write_auth = [Depends(get_current_user)]
WRITE_ERRORS = {401: {"model": ErrorResponse, "description": "Authentication required"}}
RESOURCE_ERRORS = {
    404: {"model": ErrorResponse, "description": "Centre, test, or offering not found"}
}
ResourceId = Annotated[int, Path(gt=0, le=2_147_483_647)]
Price = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]


class CentreInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=300)


class CentreUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def nonempty_update(self):
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null field")
        return self


class CentreResponse(CentreInput):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)


class TestResponse(TestInput):
    model_config = ConfigDict(from_attributes=True)
    id: int


class OfferingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test_id: int = Field(gt=0, le=2_147_483_647, strict=True)
    price: Price


class OfferingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    price: Price


class OfferingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    centre_id: int
    test_id: int
    price: Decimal


def find_centre(session: Session, centre_id: int) -> DiagnosticCentre:
    centre = session.get(DiagnosticCentre, centre_id)
    if centre is None:
        raise AppError(404, "centre_not_found", "Diagnostic centre not found")
    return centre


@router.post(
    "/centres",
    response_model=CentreResponse,
    status_code=201,
    dependencies=catalogue_write_auth,
    responses=WRITE_ERRORS,
)
def create_centre(payload: CentreInput, session: DbSession):
    centre = DiagnosticCentre(**payload.model_dump())
    session.add(centre)
    session.commit()
    return centre


@router.get("/centres", response_model=Page[CentreResponse], summary="List diagnostic centres")
def list_centres(session: DbSession, pagination: PageQuery):
    return paginate(session, select(DiagnosticCentre).order_by(DiagnosticCentre.id), pagination)


@router.get("/centres/{centre_id}", response_model=CentreResponse, responses=RESOURCE_ERRORS)
def get_centre(centre_id: ResourceId, session: DbSession):
    return find_centre(session, centre_id)


@router.patch(
    "/centres/{centre_id}",
    response_model=CentreResponse,
    dependencies=catalogue_write_auth,
    responses={**WRITE_ERRORS, **RESOURCE_ERRORS},
)
def update_centre(centre_id: ResourceId, payload: CentreUpdate, session: DbSession):
    centre = find_centre(session, centre_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(centre, field, value)
    session.commit()
    return centre


@router.post(
    "/tests",
    response_model=TestResponse,
    status_code=201,
    dependencies=catalogue_write_auth,
    responses=WRITE_ERRORS,
)
def create_test(payload: TestInput, session: DbSession):
    test = DiagnosticTest(**payload.model_dump())
    session.add(test)
    session.commit()
    return test


@router.get("/tests", response_model=Page[TestResponse], summary="List diagnostic tests")
def list_tests(session: DbSession, pagination: PageQuery):
    return paginate(session, select(DiagnosticTest).order_by(DiagnosticTest.id), pagination)


@router.post(
    "/centres/{centre_id}/tests",
    response_model=OfferingResponse,
    status_code=201,
    dependencies=catalogue_write_auth,
    responses={
        **WRITE_ERRORS,
        **RESOURCE_ERRORS,
        409: {"model": ErrorResponse, "description": "Centre already offers this test"},
    },
)
def create_offering(centre_id: ResourceId, payload: OfferingInput, session: DbSession):
    find_centre(session, centre_id)
    if session.get(DiagnosticTest, payload.test_id) is None:
        raise AppError(404, "test_not_found", "Diagnostic test not found")
    offering = CentreTest(centre_id=centre_id, **payload.model_dump())
    session.add(offering)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AppError(409, "duplicate_offering", "Centre already offers this test") from exc
    return offering


@router.get(
    "/centres/{centre_id}/tests",
    response_model=Page[OfferingResponse],
    summary="List a centre's test offerings",
    responses=RESOURCE_ERRORS,
)
def list_offerings(centre_id: ResourceId, session: DbSession, pagination: PageQuery):
    find_centre(session, centre_id)
    return paginate(
        session,
        select(CentreTest).where(CentreTest.centre_id == centre_id).order_by(CentreTest.id),
        pagination,
    )


@router.patch(
    "/centres/{centre_id}/tests/{test_id}",
    response_model=OfferingResponse,
    dependencies=catalogue_write_auth,
    responses={**WRITE_ERRORS, **RESOURCE_ERRORS},
)
def update_offering(
    centre_id: ResourceId,
    test_id: ResourceId,
    payload: OfferingUpdate,
    session: DbSession,
):
    find_centre(session, centre_id)
    offering = session.scalar(
        select(CentreTest).where(
            CentreTest.centre_id == centre_id,
            CentreTest.test_id == test_id,
        )
    )
    if offering is None:
        raise AppError(404, "offering_not_found", "Centre test offering not found")
    offering.price = payload.price
    session.commit()
    return offering
