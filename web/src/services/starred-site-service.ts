import api from '@/utils/api';
import { registerNextServer } from '@/utils/register-server';
import request from '@/utils/request';

const { starredSites, starredSite } = api;

const methods = {
  listStarredSites: {
    url: starredSites,
    method: 'get',
  },
  createStarredSite: {
    url: starredSites,
    method: 'post',
  },
} as const;

const starredSiteService = registerNextServer<keyof typeof methods>(methods);

export const deleteStarredSite = (id: string) => {
  return request.delete(starredSite(id));
};

export default starredSiteService;
