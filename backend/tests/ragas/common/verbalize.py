"""
Verbalizer: ParsedResumeResult -> natural-language statements.

RAGAS metrics decompose the response/reference into claims, so raw JSON makes
a poor input — empty fields ("twitter_url": "") become noise claims and JSON
syntax dilutes the statements. This module renders only the POPULATED fields
of a parsed resume as one plain-English statement per line.

The SAME function must be applied to both the agent's answer (``response``)
and the hand-verified ground truth (``reference``) so answer_correctness
compares like-for-like. Bump VERBALIZER_ID whenever the wording changes —
scores are only comparable between runs with the same verbalizer id.
"""

from app.services.resume_parser import ParsedResumeResult

VERBALIZER_ID = "resume-verbalizer-v1"


def _join(items: list[str]) -> str:
    return ", ".join(item for item in items if item)


def verbalize_parsed_resume(result: ParsedResumeResult) -> str:
    """Render the populated fields of ``result`` as one statement per line."""
    lines: list[str] = []

    if result.name:
        lines.append(f"The applicant's name is {result.name}.")
    if result.email:
        lines.append(f"The applicant's email address is {result.email}.")
    if result.phone:
        lines.append(f"The applicant's phone number is {result.phone}.")
    if result.address:
        lines.append(f"The applicant's address is {result.address}.")
    if result.city:
        lines.append(f"The applicant lives in the city {result.city}.")
    if result.country:
        lines.append(f"The applicant lives in the country {result.country}.")
    if result.language:
        lines.append(f"The resume is written in {result.language}.")
    if result.spoken_languages:
        lines.append(f"The applicant speaks {_join(result.spoken_languages)}.")
    if result.nationality:
        lines.append(f"The applicant's nationality is {result.nationality}.")
    if result.date_of_birth:
        lines.append(f"The applicant's date of birth is {result.date_of_birth}.")
    if result.approximate_age is not None:
        lines.append(f"The applicant is approximately {result.approximate_age} years old.")
    if result.work_authorization:
        lines.append(f"The applicant's work authorization: {result.work_authorization}.")

    if result.linkedin_url:
        lines.append(f"The applicant's LinkedIn profile is {result.linkedin_url}.")
    if result.github_url:
        lines.append(f"The applicant's GitHub profile is {result.github_url}.")
    if result.twitter_url:
        lines.append(f"The applicant's Twitter profile is {result.twitter_url}.")
    if result.website_url:
        lines.append(f"The applicant's website is {result.website_url}.")
    if result.kaggle_url:
        lines.append(f"The applicant's Kaggle profile is {result.kaggle_url}.")

    if result.summary_objective:
        lines.append(f"The resume's summary/objective states: {result.summary_objective}")
    if result.brief_summary:
        lines.append(f"In brief: {result.brief_summary}")
    if result.years_of_experience:
        lines.append(
            f"The applicant has {result.years_of_experience} years of professional experience."
        )
    if result.has_remote_work_experience:
        remote = f" ({result.remote_work_type})" if result.remote_work_type else ""
        lines.append(f"The applicant has remote work experience{remote}.")
    if result.has_management_experience:
        level = f" at the {result.management_level} level" if result.management_level else ""
        lines.append(f"The applicant has management experience{level}.")

    for honor in result.honors_and_awards:
        lines.append(f"The applicant received the honor or award: {honor}.")
    for cert in result.courses_and_certifications:
        lines.append(f"The applicant completed the course or certification: {cert}.")
    for license_ in result.drivers_licenses:
        lines.append(f"The applicant holds the driver's license: {license_}.")
    for interest in result.interests_hobbies:
        lines.append(f"The applicant's interests include {interest}.")
    for volunteer in result.volunteer_experience:
        lines.append(f"The applicant has volunteer experience: {volunteer}.")

    for position in result.positions:
        parts = []
        if position.position_name:
            parts.append(f"The applicant worked as {position.position_name}")
        else:
            parts.append("The applicant held a position")
        if position.company_name:
            parts.append(f"at {position.company_name}")
        if position.country:
            parts.append(f"in {position.country}")
        if position.start_date:
            parts.append(f"from {position.start_date}")
            parts.append(f"until {position.end_date}" if position.end_date else "until the present")
        sentence = " ".join(parts)
        if position.job_type:
            sentence += f" ({position.job_type})"
        lines.append(sentence + ".")
        if position.skills:
            lines.append(
                f"In the {position.position_name or 'position'} role at "
                f"{position.company_name or 'that company'}, the applicant used: "
                f"{_join(position.skills)}."
            )
        if position.job_details:
            lines.append(
                f"Details of the {position.position_name or 'position'} role at "
                f"{position.company_name or 'that company'}: {position.job_details}"
            )

    for education in result.education_qualifications:
        parts = ["The applicant studied"]
        if education.degree_type:
            parts.append(f"a {education.degree_type}")
        if education.faculty_department:
            parts.append(f"in {education.faculty_department}")
        if education.specialization_subjects:
            parts.append(f"specializing in {education.specialization_subjects}")
        if education.school_name:
            parts.append(f"at {education.school_name}")
        if education.country:
            parts.append(f"in {education.country}")
        if education.start_date:
            parts.append(f"from {education.start_date}")
        if education.end_date:
            parts.append(f"until {education.end_date}")
        if education.learning_mode:
            parts.append(f"({education.learning_mode})")
        lines.append(" ".join(parts) + ".")
        if education.education_details:
            lines.append(
                f"Details of the education at {education.school_name or 'that school'}: "
                f"{education.education_details}"
            )

    for project in result.projects:
        if not (project.project_name or project.description):
            continue
        sentence = f"The applicant built the project '{project.project_name or 'unnamed'}'"
        if project.description:
            sentence += f": {project.description}"
        lines.append(sentence.rstrip(".") + ".")
        if project.url:
            lines.append(f"The project '{project.project_name}' is available at {project.url}.")

    for publication in result.publications:
        if not publication.title:
            continue
        sentence = f"The applicant published '{publication.title}'"
        if publication.publisher:
            sentence += f" in {publication.publisher}"
        if publication.date:
            sentence += f" on {publication.date}"
        lines.append(sentence + ".")
        if publication.url:
            lines.append(f"The publication '{publication.title}' is available at {publication.url}.")

    for category, skills in result.skills.items():
        if not skills:
            continue
        if category == "Default":
            lines.append(f"The applicant's skills include: {_join(skills)}.")
        else:
            lines.append(f"The applicant's skills in '{category}' include: {_join(skills)}.")

    return "\n".join(lines)
