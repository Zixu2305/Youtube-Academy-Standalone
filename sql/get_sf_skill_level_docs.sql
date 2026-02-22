/*
Build one embedding document per (sf_skill_id, proficiency_level).

Tip: For large datasets, run:
  SET SESSION group_concat_max_len = 1024 * 1024;
before executing this query.
*/
SELECT
  CONCAT(sl.sf_skill_id, ':', sl.proficiency_level) AS point_id,
  sl.sf_skill_id,
  sl.proficiency_level,
  s.tsc_ccs_code,
  s.title AS skill_title,
  s.category,
  s.skill_type,
  s.is_retired,
  COALESCE(item_blocks.knowledge_block, '') AS knowledge_block,
  COALESCE(item_blocks.ability_block, '') AS ability_block,
  CONCAT_WS(
    '\n\n',
    CONCAT('Skill title: ', s.title),
    CONCAT('Skill description: ', COALESCE(s.description, '')),
    CONCAT('Category: ', COALESCE(s.category, '')),
    CONCAT('TSC/CCS code: ', COALESCE(s.tsc_ccs_code, '')),
    CONCAT('Skill type: ', COALESCE(s.skill_type, '')),
    CONCAT('Retired: ', IF(s.is_retired = 1, 'yes', 'no')),
    CONCAT('Proficiency level: ', sl.proficiency_level),
    CONCAT('Proficiency description: ', COALESCE(sl.proficiency_description, '')),
    CONCAT('Knowledge items:\n', COALESCE(item_blocks.knowledge_block, '')),
    CONCAT('Ability items:\n', COALESCE(item_blocks.ability_block, ''))
  ) AS embed_text
FROM sf_skill_level sl
INNER JOIN sf_skill s
  ON s.sf_skill_id = sl.sf_skill_id
LEFT JOIN (
  SELECT
    ci.sf_skill_id,
    ci.proficiency_level,
    GROUP_CONCAT(
      CASE WHEN ci.item_type = 'knowledge' THEN ci.item_text END
      ORDER BY ci.item_id SEPARATOR '\n'
    ) AS knowledge_block,
    GROUP_CONCAT(
      CASE WHEN ci.item_type = 'ability' THEN ci.item_text END
      ORDER BY ci.item_id SEPARATOR '\n'
    ) AS ability_block
  FROM sf_competency_item ci
  GROUP BY ci.sf_skill_id, ci.proficiency_level
) item_blocks
  ON item_blocks.sf_skill_id = sl.sf_skill_id
  AND item_blocks.proficiency_level = sl.proficiency_level
ORDER BY sl.sf_skill_id, sl.proficiency_level;
