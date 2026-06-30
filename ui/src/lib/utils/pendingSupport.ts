export interface PendingSupportData {
	id: string;
	user_id: string;
	timestamp: number;
	attempts: number;
}

type SupportOwner = Pick<PendingSupportData, 'id' | 'user_id'>;

const PENDING_SUPPORT_MAX_AGE_MS = 30 * 60 * 1000;

export const createPendingSupportData = (
	support: SupportOwner,
	now = Date.now()
): PendingSupportData => ({
	id: support.id,
	user_id: support.user_id,
	timestamp: now,
	attempts: 0
});

export const parsePendingSupportData = (
	raw: string | null,
	userId: string,
	now = Date.now()
): PendingSupportData | null => {
	if (!raw || !userId) return null;

	try {
		const data = JSON.parse(raw);
		if (
			typeof data.id !== 'string' ||
			data.user_id !== userId ||
			typeof data.timestamp !== 'number' ||
			data.timestamp > now ||
			now - data.timestamp >= PENDING_SUPPORT_MAX_AGE_MS
		) {
			return null;
		}
		return { ...data, attempts: Number.isInteger(data.attempts) ? data.attempts : 0 };
	} catch {
		return null;
	}
};
