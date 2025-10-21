# -*- coding: utf-8 -*-
"""
统一深度分析 API
提供文章保存、分析触发、报告查询等接口
"""

import logging
import hashlib
import asyncio
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.db.database import get_db
from app.models.models import Article, AnalysisReport
from app.services.unified_analysis_service import UnifiedAnalysisService

logger = logging.getLogger(__name__)

router = APIRouter()


class SaveArticleWithAnalysisRequest(BaseModel):
    """保存文章并触发分析的请求"""
    title: str
    author: Optional[str] = None
    source_url: Optional[str] = None
    content: str
    user_id: Optional[int] = None


class AnalysisStatusResponse(BaseModel):
    """分析状态响应"""
    status: str  # pending, processing, completed, failed
    progress: Optional[int] = None  # 0-100
    error_message: Optional[str] = None


@router.post("/api/v1/articles/save-with-analysis")
async def save_article_with_analysis(
    request: SaveArticleWithAnalysisRequest,
    db: Session = Depends(get_db)
):
    """
    保存文章并触发深度分析（同步执行）

    工作流程：
    1. 保存文章到数据库（MD5去重）
    2. 检查是否已有分析报告
    3. 如果没有报告，立即执行分析
    4. 返回文章ID和分析状态

    Returns:
        {
            "article": {
                "id": int,
                "is_new": bool
            },
            "analysis": {
                "status": "completed" | "pending",
                "task_id": None
            }
        }
    """
    # 1. 计算内容哈希
    content_hash = hashlib.md5(request.content.encode('utf-8')).hexdigest()

    # 2. 检查文章是否已存在
    article = db.query(Article).filter(Article.content_hash == content_hash).first()
    is_new_article = False

    if article:
        # 文章已存在，更新阅读信息
        article.last_read_at = datetime.utcnow()
        article.read_count += 1
        db.commit()

        # 检查是否已有分析报告
        report = db.query(AnalysisReport).filter(
            AnalysisReport.article_id == article.id
        ).first()

        if report and report.status == 'completed':
            # 已有完整报告，直接返回
            logger.info(f"文章已存在且有分析报告，ID: {article.id}")
            return {
                "article": {
                    "id": article.id,
                    "is_new": False
                },
                "analysis": {
                    "status": "completed",
                    "task_id": None
                }
            }

    else:
        # 创建新文章
        is_new_article = True
        article = Article(
            user_id=request.user_id,
            title=request.title,
            author=request.author,
            source_url=request.source_url,
            content=request.content,
            content_hash=content_hash,
            word_count=len(request.content)
        )
        db.add(article)
        db.commit()
        db.refresh(article)

        logger.info(f"新文章已保存，ID: {article.id}")

    # 3. 创建或获取分析报告记录
    report = db.query(AnalysisReport).filter(
        AnalysisReport.article_id == article.id
    ).first()

    if not report:
        report = AnalysisReport(
            article_id=article.id,
            status='pending'
        )
        db.add(report)
        db.commit()
        db.refresh(report)

    # 4. 执行同步分析任务（开发环境）
    try:
        logger.info(f"开始分析文章，ID: {article.id}")

        # 更新状态为处理中
        report.status = 'processing'
        db.commit()

        # 创建分析服务实例
        analysis_service = UnifiedAnalysisService()

        # 执行分析
        result = await analysis_service.analyze_article(
            article_content=article.content,
            article_title=article.title or ""
        )

        # 保存分析结果
        report.status = 'completed'
        report.report_data = result['report']
        report.metadata = result['metadata']
        report.completed_at = datetime.utcnow()
        db.commit()

        logger.info(f"文章分析完成，ID: {article.id}")

        return {
            "article": {
                "id": article.id,
                "is_new": is_new_article
            },
            "analysis": {
                "status": "completed",
                "task_id": None
            }
        }

    except Exception as e:
        # 分析失败，记录错误
        report.status = 'failed'
        report.error_message = str(e)
        db.commit()

        logger.error(f"文章分析失败: {str(e)}")

        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")


@router.get("/api/v1/articles/{article_id}/analysis-status")
async def get_analysis_status(
    article_id: int,
    db: Session = Depends(get_db)
):
    """
    获取文章分析状态

    Returns:
        {
            "status": "pending" | "processing" | "completed" | "failed",
            "progress": 0-100,
            "error_message": str | null
        }
    """
    report = db.query(AnalysisReport).filter(
        AnalysisReport.article_id == article_id
    ).first()

    if not report:
        return {
            "status": "not_started",
            "progress": 0,
            "error_message": None
        }

    return {
        "status": report.status,
        "progress": 100 if report.status == 'completed' else (50 if report.status == 'processing' else 0),
        "error_message": report.error_message
    }


@router.get("/api/v1/articles/{article_id}/analysis-report")
async def get_analysis_report(
    article_id: int,
    db: Session = Depends(get_db)
):
    """
    获取文章的完整分析报告

    Returns:
        {
            "report_data": {...},  # 完整的JSON报告
            "metadata": {
                "model_used": str,
                "tokens_used": int,
                "processing_time_ms": int,
                "completed_at": datetime
            }
        }
    """
    report = db.query(AnalysisReport).filter(
        AnalysisReport.article_id == article_id,
        AnalysisReport.status == 'completed'
    ).first()

    if not report:
        raise HTTPException(
            status_code=404,
            detail="分析报告不存在或尚未完成"
        )

    return {
        "report_data": report.report_data,
        "metadata": {
            "model_used": report.model_used,
            "tokens_used": report.tokens_used,
            "processing_time_ms": report.processing_time_ms,
            "completed_at": report.completed_at.isoformat() if report.completed_at else None
        }
    }


@router.post("/api/v1/articles/{article_id}/reanalyze")
async def reanalyze_article(
    article_id: int,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    重新分析文章（例如当分析失败或需要更新时）

    Returns:
        {
            "task_id": str,
            "message": "重新分析已开始"
        }
    """
    # 检查文章是否存在
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")

    # 重置分析报告状态
    report = db.query(AnalysisReport).filter(
        AnalysisReport.article_id == article_id
    ).first()

    if report:
        report.status = 'pending'
        report.error_message = None
    else:
        report = AnalysisReport(
            article_id=article_id,
            status='pending'
        )
        db.add(report)

    db.commit()

    # 触发异步任务
    task = analyze_article_task.delay(article_id, user_id)

    return {
        "task_id": task.id,
        "message": "重新分析已开始"
    }
