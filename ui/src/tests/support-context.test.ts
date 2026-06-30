import { describe, expect, it } from 'vitest';
import { createPendingSupportData, parsePendingSupportData } from '$lib/utils/pendingSupport';

describe('pending support context', () => {
	const now = 2_000_000;
	const support = { id: 'support-a', user_id: 'student-a' };

	it('accepts only fresh context owned by the current student', () => {
		const pending = createPendingSupportData(support, now);
		const raw = JSON.stringify(pending);

		expect(parsePendingSupportData(raw, 'student-a', now)).toEqual(pending);
		expect(parsePendingSupportData(raw, 'student-b', now)).toBeNull();
		expect(parsePendingSupportData(raw, 'student-a', now + 30 * 60 * 1000)).toBeNull();
	});
});
