# Batch YouTube Ingestion Progress

## Status: Paused (API Quota Exceeded)
**Date:** 2026-02-25
**Processed:** 17/350 competencies (quota hit at competency 17)
**Total videos in MongoDB:** 42
**Total videos embedded in Qdrant:** 42

## Target Skills (Infocomm Technology)
1. Artificial Intelligence Ethics and Governance
2. Data Analytics
3. Data Engineering
4. Design Thinking Practice
5. Networking
6. Software Testing
7. Solution Architecture
8. User Interface Design

## Completed Competencies

### Artificial Intelligence Ethics and Governance

#### Level 2 (7 competencies - DONE)
| Competency | Videos |
|---|---|
| ability: Identify AI Ethics and Governance principles and processes | 1 |
| ability: Identify the linkages and apply the growing importance of AI Ethics and Governance | 1 |
| ability: Uphold and comply with relevant AI Ethics and Governance regulations | 0 (dupes) |
| knowledge: AI Ethics and Governance frameworks | 0 (dupes) |
| knowledge: AI Ethics and Governance principles | 3 |
| knowledge: AI Ethics and Governance processes | 2 |
| knowledge: Relevant code of conduct for AI Ethics and Governance | 3 |

#### Level 3 (7 competencies - DONE)
| Competency | Videos |
|---|---|
| ability: Apply safeguards to deter situations that may result in AI Ethics breaches | 0 (dupes) |
| ability: Develop plans to negate occurrence of AI Ethics and Governance breaches | 1 |
| ability: Identify implications for non-adherence or breach of AI Ethics and Governance | 0 (dupes) |
| ability: Identify situations which may give rise to ethical conflicts | 1 |
| ability: Monitor AI components within projects and check for adherence | 2 |
| knowledge: AI Ethics and Governance principles and market best practices | 3 |
| knowledge: Organisation's AI ethical culture | 1 |

#### Level 4 (3 competencies - PARTIAL, quota hit)
| Competency | Videos |
|---|---|
| ability: Articulate how AI should be used to stakeholders | 5 |
| ability: Evaluate deployed models for transparency for ease of explanation | 5 |
| ability: Interpret and implement AI Ethics and Governance principles | quota hit |

## Remaining Skills (NOT YET STARTED)
| Skill | Competencies | Status |
|---|---|---|
| Data Analytics | TBD | Not started |
| Data Engineering | TBD | Not started |
| Design Thinking Practice | TBD | Not started |
| Networking | TBD | Not started |
| Software Testing | TBD | Not started |
| Solution Architecture | TBD | Not started |
| User Interface Design | TBD | Not started |

## To Resume
YouTube API quota resets daily at midnight Pacific Time. Re-run:
```bash
# 1. Ingest more videos (resumes from where it stopped — existing videos updated, not duplicated)
python scripts/batch_ingest_yt.py --api-key YOUR_YOUTUBE_API_KEY

# 2. After ingestion, embed new videos into Qdrant
python scripts/embed_yt_videos.py
```

## Quota Estimation
- 350 competencies × ~600 quota units each ≈ 210,000 units total
- Daily quota: 10,000 units
- Estimated days to complete: ~21 days (running once per day)

## Port Configuration Note
MongoDB Docker is on port **27018** (not the default 27017) to avoid conflict with a local MongoDB instance.
