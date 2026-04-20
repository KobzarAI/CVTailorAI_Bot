from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class FlexibleBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PersonalInfoModel(FlexibleBaseModel):
    full_name: str = ""
    email: str = ""
    location: str = ""
    linkedin: str = ""
    portfolio: str = ""


class TermRecordModel(FlexibleBaseModel):
    term: str
    confirmed_by: list[int] = Field(default_factory=list)
    origin: bool | None = None


class SkillsModel(FlexibleBaseModel):
    hard_skills: list[TermRecordModel] = Field(default_factory=list)
    soft_skills: list[TermRecordModel] = Field(default_factory=list)


class TermBucketsModel(FlexibleBaseModel):
    skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class UnknownTermsModel(FlexibleBaseModel):
    skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class BulletModel(FlexibleBaseModel):
    id: int | None = None
    text: str = ""
    skills_used: list[str] = Field(default_factory=list)
    keyword_used: list[str] = Field(default_factory=list)


class ExperienceEntryModel(FlexibleBaseModel):
    company: str = ""
    job_title: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    duration_years: float | None = None
    bullets: list[BulletModel] = Field(default_factory=list)


class MasterResumeModel(FlexibleBaseModel):
    personal_info: PersonalInfoModel = Field(default_factory=PersonalInfoModel)
    desired_positions: list[str] = Field(default_factory=list)
    skills: SkillsModel = Field(default_factory=SkillsModel)
    keywords: list[TermRecordModel] = Field(default_factory=list)
    experience: list[ExperienceEntryModel] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    certifications: list[dict[str, Any]] = Field(default_factory=list)
    languages: list[dict[str, Any]] = Field(default_factory=list)
    unconfirmed: TermBucketsModel = Field(default_factory=TermBucketsModel)
    explicitly_not_used: TermBucketsModel = Field(default_factory=TermBucketsModel)
    unknown: UnknownTermsModel = Field(default_factory=UnknownTermsModel)


class ExtractTermModel(FlexibleBaseModel):
    term: str
    synonyms: list[str] = Field(default_factory=list)
    type: str | None = None
    priority: int | None = None


class ExtractBucketModel(FlexibleBaseModel):
    skills: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class ExtractModel(FlexibleBaseModel):
    job_title: str | None = None
    required_skills: list[ExtractTermModel] = Field(default_factory=list)
    required_keywords: list[ExtractTermModel] = Field(default_factory=list)
    mandatory: ExtractBucketModel = Field(default_factory=ExtractBucketModel)
    nice_to_have: ExtractBucketModel = Field(default_factory=ExtractBucketModel)


class TermsMergeItemModel(FlexibleBaseModel):
    term: str
    company: str = ""
    type: Literal["skill", "keyword"]
    generated_bullet: str = ""
    used: bool = False


class TermsMergePayloadModel(FlexibleBaseModel):
    terms: list[TermsMergeItemModel] = Field(default_factory=list)


class MergeRequestModel(FlexibleBaseModel):
    json1: MasterResumeModel
    json2: TermsMergePayloadModel


class GoogleDocRequestModel(FlexibleBaseModel):
    content: list[dict[str, Any]] = Field(default_factory=list)


class GoogleDocResponseModel(FlexibleBaseModel):
    requests: list[dict[str, Any]] = Field(default_factory=list)


class FindGapsRequestModel(FlexibleBaseModel):
    extract: ExtractModel
    master_resume: MasterResumeModel


class GenerateAdaptedResumeRequestModel(FlexibleBaseModel):
    extract: ExtractModel
    extended_master_resume: MasterResumeModel


class GenerateAdaptedResumeResponseModel(FlexibleBaseModel):
    adapted_resume: MasterResumeModel
    match_base: float
    match_adjusted: float
    short_extract: str
    bullets: str


class MasterResumePayloadModel(FlexibleBaseModel):
    master_resume: MasterResumeModel


class GeneratedTermModel(FlexibleBaseModel):
    term: str
    type: str
    generated_bullet: str = ""


class GeneratedTermsResponseModel(FlexibleBaseModel):
    terms: list[GeneratedTermModel] = Field(default_factory=list)


class CompaniesRequestModel(FlexibleBaseModel):
    companies: list[str] = Field(default_factory=list)


class InlineKeyboardButtonModel(FlexibleBaseModel):
    text: str
    callback_data: str


class InlineKeyboardResponseModel(FlexibleBaseModel):
    inline_keyboard: list[list[InlineKeyboardButtonModel]] = Field(default_factory=list)


class BulletReferenceModel(FlexibleBaseModel):
    id: int
    text: str


class ToConfirmItemModel(FlexibleBaseModel):
    term: str
    type: str
    confirmed_by: list[int] = Field(default_factory=list)


class SelectToConfirmResponseModel(FlexibleBaseModel):
    ToConfirm_list: list[ToConfirmItemModel] = Field(default_factory=list)
    Bullets: list[BulletReferenceModel] = Field(default_factory=list)


class AutoConfirmRequestModel(FlexibleBaseModel):
    master_resume: MasterResumeModel
    ToConfirm_list: list[ToConfirmItemModel] = Field(default_factory=list)


class RemoveDuplicatesRequestModel(FlexibleBaseModel):
    duplicates: list[str] = Field(default_factory=list)
    master_resume: MasterResumeModel


class TextResponseModel(RootModel[str]):
    pass


class BulletTextUpdateModel(FlexibleBaseModel):
    id: int
    text: str


class PushBulletsRequestModel(FlexibleBaseModel):
    bullets: list[BulletTextUpdateModel] = Field(default_factory=list)
    master_resume: MasterResumeModel


class ATSScoreRequestModel(FlexibleBaseModel):
    job_text: str = Field(min_length=1)
    resume_text: str = Field(min_length=1)


class ATSMetricsResponseModel(FlexibleBaseModel):
    ats_score: float = Field(alias="ats_score(70-90)")
    semantic: float = Field(alias="semantic(coverage_incl_synonyms_0.6-0.85)")
    recall: float = Field(alias="recall(JD->CV_0.6-0.85)")
    precision: float = Field(alias="precision(density_of_terms_0.4-0.7)")
    overlap_keywords: list[str] = Field(default_factory=list)
    job_keywords: list[str] = Field(default_factory=list)
    resume_keywords: list[str] = Field(default_factory=list)


class AnalyzeJobRequestModel(FlexibleBaseModel):
    job_description: str = Field(min_length=1)
    extract: ExtractModel


class AnalyzeJobResponseModel(FlexibleBaseModel):
    match_percent: float
    mandatory: str
    nice_to_have: str
    lost: list[str] = Field(default_factory=list)


class ClassifiedSkillsModel(FlexibleBaseModel):
    hard_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)


class SkillsToMasterRequestModel(FlexibleBaseModel):
    skills: ClassifiedSkillsModel
    master_resume: MasterResumeModel


class BulletsToButtonsRequestModel(FlexibleBaseModel):
    bullets: list[BulletReferenceModel] = Field(default_factory=list)


class TermNotUsedRequestModel(FlexibleBaseModel):
    term_name: str = Field(min_length=1)
    term_type: Literal["hard", "soft", "keyword"]
    master_resume: MasterResumeModel


class GetCompanyBulletsRequestModel(FlexibleBaseModel):
    company_name: str = Field(min_length=1)
    master_resume: MasterResumeModel


class CompanyBulletsResponseModel(FlexibleBaseModel):
    bullets_text: str
    bullets_menu: InlineKeyboardResponseModel


class ConfirmTermRequestModel(FlexibleBaseModel):
    bullet_id: int
    term_name: str = Field(min_length=1)
    term_type: Literal["hard", "soft", "keyword"]
    master_resume: MasterResumeModel


class AddNewBulletRequestModel(FlexibleBaseModel):
    company: str = Field(min_length=1)
    bullet: str = Field(min_length=1)
    term_name: str = Field(min_length=1)
    term_type: Literal["hard", "soft", "keyword"]
    master_resume: MasterResumeModel


class HealthResponseModel(FlexibleBaseModel):
    status: str
