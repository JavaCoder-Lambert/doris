-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements.  See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership.  The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License.  You may obtain a copy of the License at
--
--   http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing,
-- software distributed under the License is distributed on an
-- "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
-- KIND, either express or implied.  See the License for the
-- specific language governing permissions and limitations
-- under the License.

-- Replacement query tail for an existing WITH ... q_raw AS (...) definition.
-- Remove the q_key_profile CTE and its preceding comma if no longer referenced.
-- q_raw itself is not shown in the screenshot and must be retained by the caller.
-- The helper name __q_key_rows must not already exist in q_raw.
-- This keeps only groups of size ONE, not one arbitrary row per group.
SELECT * EXCEPT (`__q_key_rows`)
FROM (
    SELECT
        r.*,
        1 AS `__q_present`,
        COUNT(*) OVER (
            PARTITION BY
                r.`dt_month`,
                r.`Platform_Order_Number`,
                r.`Ware_Shop_Code`,
                r.`Shop_Code`,
                r.`Seller_Sku`,
                r.`MSKU`
        ) AS `__q_key_rows`
    FROM q_raw r
) counted
WHERE `__q_key_rows` = 1;
