// JavaScript for AI Essay Grading System

document.addEventListener('DOMContentLoaded', function() {
    // Enable Bootstrap tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    const tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
    
    // Highlight active navigation item
    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll('.navbar-nav .nav-link');
    
    navLinks.forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
            link.setAttribute('aria-current', 'page');
        }
    });
    
    // Dynamic textarea sizing
    const textareas = document.querySelectorAll('textarea');
    
    textareas.forEach(textarea => {
        textarea.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = (this.scrollHeight) + 'px';
        });
    });
    
    // Update total points when individual criteria points change
    const pointInputs = document.querySelectorAll('input[name^="points"]');
    
    if (pointInputs.length > 0) {
        pointInputs.forEach(input => {
            input.addEventListener('change', updateTotalPoints);
        });
    }
    
    function updateTotalPoints() {
        let total = 0;
        let maxTotal = 0;
        
        pointInputs.forEach(input => {
            const points = parseInt(input.value) || 0;
            const maxPoints = parseInt(input.nextElementSibling.textContent.replace('/', '')) || 0;
            
            total += points;
            maxTotal += maxPoints;
        });
        
        const percentage = Math.round((total / maxTotal) * 100);
        
        // If there's a display for total points, update it
        const totalPointsDisplay = document.querySelector('.total-points');
        if (totalPointsDisplay) {
            totalPointsDisplay.textContent = `${total}/${maxTotal} (${percentage}%)`;
        }
    }
    
    // Add confirmation for form submissions
    const forms = document.querySelectorAll('form:not(.no-confirm)');
    
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            if (form.classList.contains('needs-confirmation') && !confirm('Are you sure you want to submit this form?')) {
                e.preventDefault();
                return false;
            }
        });
    });
    
    // For demo: Auto-fill login form with demo credentials
    const demoFillBtn = document.getElementById('demo-fill');
    if (demoFillBtn) {
        demoFillBtn.addEventListener('click', function() {
            document.getElementById('email').value = 'teacher@example.com';
            document.getElementById('password').value = 'password123';
        });
    }
});
