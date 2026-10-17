"""在调用方写事务内回收失去全部引用的 remote 制品元数据。"""

from sqlalchemy import select

from app.storage.skill_variant_models import SkillContentVariantRecord
from app.storage.unit_of_work import UnitOfWork


def discard_unreferenced_remote_artifacts(
    unit: UnitOfWork, artifact_ids: set[int]
) -> list[str]:
    """引用删除并 flush 后调用；返回提交成功后可清理的文件路径。"""
    paths = []
    for artifact_id in artifact_ids:
        referenced = unit.session.scalar(
            select(SkillContentVariantRecord.id).where(
                SkillContentVariantRecord.remote_artifact_id == artifact_id
            )
        )
        if referenced is not None:
            continue
        artifact = unit.skill_variants.get_remote_artifact(artifact_id)
        if artifact is not None:
            paths.append(artifact.relative_path)
            unit.session.delete(artifact)
    return paths
