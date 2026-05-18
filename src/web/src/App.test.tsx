import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';

const defaultRequest = {
  fellow_groups: {
    NCC_JR: ['NCC Raya', 'NCC Joseph'],
    NCC_SR: ['NCC David', 'NCC Prash'],
    STROKE: ['Stroke Gabi'],
    CCM: ['CCM Ariana'],
    NH: ['NH Adam'],
    LIA: ['NCC Lia'],
  },
  shifts: ['NCC1', 'NCC2', 'Swing', 'MICU', 'Stroke', 'Telestroke/Clinic'],
  fellow_week_pairs: {
    'NCC Raya': [21],
    'Stroke Gabi': [17],
  },
};

const scheduleResult = {
  request: defaultRequest,
  shifts_for_fellows: {
    'NCC Raya': ['MICU', 'NCC1'],
    'NCC Joseph': ['MICU', 'NCC2'],
    'Stroke Gabi': ['Stroke', 'Telestroke/Clinic'],
  },
  fellows_for_shifts: {
    NCC1: ['NCC Raya', ''],
    NCC2: ['NCC Joseph', ''],
    Extra: ['', ''],
    Swing: ['', ''],
    Stroke: ['Stroke Gabi', ''],
    'Telestroke/Clinic': ['', 'Stroke Gabi'],
    Stroke_Supervisory: ['', ''],
  },
};

beforeEach(() => {
  jest.spyOn(global, 'fetch').mockImplementation((input, init) => {
    const url = input.toString();
    if (url.endsWith('/api/default-schedule')) {
      return Promise.resolve(new Response(JSON.stringify(defaultRequest), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    }
    if (url.endsWith('/api/schedule') && init?.method === 'POST') {
      return Promise.resolve(new Response(JSON.stringify(scheduleResult), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    }
    return Promise.reject(new Error(`Unhandled fetch: ${url}`));
  });
});

afterEach(() => {
  jest.restoreAllMocks();
});

test('loads seeded fellow groups from the backend', async () => {
  render(<App />);

  expect(await screen.findByRole('heading', { name: /fellow scheduler/i })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText(/NCC_JR fellows/i)).toHaveValue('NCC Raya\nNCC Joseph'));
  expect(screen.getByLabelText(/STROKE fellows/i)).toHaveValue('Stroke Gabi');
  expect(screen.getByText('NCC Raya')).toBeInTheDocument();
  expect(screen.getByDisplayValue('21')).toBeInTheDocument();
});

test('posts schedule request and renders returned tables', async () => {
  render(<App />);

  await screen.findByLabelText(/NCC_JR fellows/i);
  await waitFor(() => expect(screen.getByRole('button', { name: /generate schedule/i })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: /generate schedule/i }));

  await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
    'http://127.0.0.1:5000/api/schedule',
    expect.objectContaining({ method: 'POST' }),
  ));
  expect(await screen.findByRole('heading', { name: /per-fellow schedule/i })).toBeInTheDocument();
  expect(screen.getAllByText('MICU').length).toBeGreaterThan(0);
  expect(screen.getAllByText('NCC Raya').length).toBeGreaterThan(0);
});
