create table if not exists building_exclusions (
  building_id text primary key,
  address text not null default '',
  region_code text not null default '',
  safety_check boolean not null default false,
  reinforcement boolean not null default false,
  excluded_at timestamptz not null default now()
);

create index if not exists building_exclusions_region on building_exclusions (region_code);
