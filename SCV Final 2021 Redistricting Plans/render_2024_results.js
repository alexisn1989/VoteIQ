/**
 * VoteIQ 2024 Election Result Renderer
 * Renders summaries for President and Senate maps safely.
 */

async function loadElectionResults(filename) {
    try {
        const response = await fetch(filename);
        const data = await response.json();
        renderResultsSummary(data);
    } catch (error) {
        console.error(`Error loading results from ${filename}:`, error);
    }
}

function renderResultsSummary(data) {
    const container = document.getElementById('results-2024-container');
    if (!container) return;

    container.innerHTML = ''; // Clear securely
    const fragment = document.createDocumentFragment();
    const year = data.election_year || '2024';

    Object.entries(data.locality_results).forEach(([raceName, localities]) => {
        const section = document.createElement('section');
        section.className = 'election-summary-card';

        const title = document.createElement('h2');
        title.textContent = `${year} ${raceName} (Statewide Summary)`;
        section.appendChild(title);

        const summary = data.statewide_summary?.[raceName] || { candidates: [] };
        const list = document.createElement('ul');
        summary.candidates.forEach(cand => {
            const li = document.createElement('li');
            li.textContent = `${cand.name}: ${cand.pct}% (${cand.votes.toLocaleString()} votes)`;
            list.appendChild(li);
        });

        section.appendChild(list);
        fragment.appendChild(section);
    });

    container.appendChild(fragment);
}

/**
 * Returns a color based on the party and margin of victory.
 * Used for statewide Virginia locality mapping.
 */
function getLocalityColor(localityData) {
    const candidates = localityData.candidates;
    if (!candidates || candidates.length < 2) return '#cccccc';

    // Sort candidates to find the winner and margin
    const winner = candidates[0];
    const runnerUp = candidates[1];
    
    const winnerPct = winner.pct;
    const runnerUpPct = runnerUp.pct;
    const margin = winnerPct - runnerUpPct;

    // Identify party based on candidate names (Standard VA 2024 candidates)
    const blueKeywords = ['Harris', 'Kaine', 'Democratic'];
    const isBlue = blueKeywords.some(key => winner.includes(key));

    if (isBlue) {
        if (margin > 20) return '#084594'; // Solid Blue
        if (margin > 10) return '#2171b5'; // Blue
        return '#6baed6'; // Lean Blue
    } else {
        if (margin > 20) return '#cb181d'; // Solid Red
        if (margin > 10) return '#ef3b2c'; // Red
        return '#fb6a4a'; // Lean Red
    }
}

/**
 * Calculates bubble radius based on total votes cast.
 * Matches the logic in main.py: 4 + sqrt(total/max) * 34
 */
function getBubbleRadius(totalVotes, maxVotesInState) {
    if (!totalVotes || !maxVotesInState) return 4;
    return 4 + Math.sqrt(totalVotes / maxVotesInState) * 34;
}

/**
 * Example integration logic for a Leaflet-based statewide map
 */
function styleMapFeature(feature, electionData) {
    const localityName = feature.properties.name;
    // Handle potential casing differences between GeoJSON and CSV
    const raceName = "President"; // Example
    const results = electionData.locality_results[raceName]?.[localityName] || 
                    electionData.locality_results[raceName]?.[localityName.toUpperCase()];
    
    // Default style for unknown data
    let fill = '#cccccc';
    
    if (results) {
        fill = getLocalityColor(results);
    }

    return {
        fillColor: fill,
        weight: 1,
        opacity: 1,
        color: 'white',
        fillOpacity: 0.8
    };
}

// Auto-initialize based on URL parameters
document.addEventListener('DOMContentLoaded', () => {
    const urlParams = new URLSearchParams(window.location.search);
    const year = urlParams.get('year') || '2024';
    const filename = `election_results_${year}.json`;
    
    loadElectionResults(filename);
});