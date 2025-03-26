import os
import google.generativeai as genai
from typing import List, Dict, Any
import json

# Configure the Gemini API
genai.configure(api_key='test-api-key')
model = genai.GenerativeModel('gemini-2.0-flash')

def grade_essay_with_llm(essay_text: str, rubric_criteria: List[Dict]) -> List[Dict]:
    """
    Grades an essay using the Gemini LLM based on provided rubric criteria.
    
    Args:
        essay_text: The text of the essay to grade.
        rubric_criteria: List of dicts with 'name', 'max_points', and 'point_descriptions' keys.
        
    Returns:
        List of dicts with grading results including points and feedback.
    """
    grades = []
    
    for criterion in rubric_criteria:
        # Format the point descriptions for the prompt
        point_descriptions = criterion.get('point_descriptions', {})
        descriptions_text = ""
        
        if point_descriptions:
            descriptions_text = "Point descriptions for this criterion:\n"
            for point, desc in sorted(point_descriptions.items(), key=lambda x: int(x[0])):
                descriptions_text += f"{point} points: {desc}\n"
        else:
            # If no point descriptions are provided, create a generic scale
            descriptions_text = "Point scale:\n"
            for point in range(1, criterion['max_points'] + 1):
                if point == 1:
                    descriptions_text += f"1 point: Poor - Fails to meet this criterion\n"
                elif point == criterion['max_points']:
                    descriptions_text += f"{point} points: Excellent - Fully meets this criterion\n"
                elif point == criterion['max_points'] // 2:
                    descriptions_text += f"{point} points: Average - Partially meets this criterion\n"
        
        # Create the prompt for the LLM
        prompt = f"""
        You are an experienced teacher grading an essay. Please evaluate the following essay based on this criterion:
        
        CRITERION: {criterion['name']}
        MAXIMUM POINTS: {criterion['max_points']}
        
        {descriptions_text}
        
        ESSAY:
        {essay_text}
        
        Based on the criterion "{criterion['name']}" and the point descriptions above, analyze the essay and determine how many points it should receive.
        
        Provide your evaluation in this exact JSON format:
        {{
          "points": [number between 1 and {criterion['max_points']}],
          "feedback": [detailed feedback explaining the score],
          "justification": [explanation of why the essay deserves this specific point level based on the rubric],
          "strengths": [list of 2-3 specific strengths],
          "areas_for_improvement": [list of 2-3 specific areas that could be improved]
        }}
        
        Return ONLY the JSON object without any other text.
        """
        
        try:
            # Get response from Gemini
            response = model.generate_content(prompt)
            
            # Parse the response
            response_text = response.text
            
            # Clean up the response to extract just the JSON part
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            import json
            try:
                result = json.loads(response_text)
                # Ensure we have all required fields
                points = min(criterion['max_points'], max(1, result.get('points', 1)))
                feedback = result.get('feedback', 'No specific feedback provided.')
                justification = result.get('justification', '')
                
                # Format the strengths and areas for improvement
                strengths = result.get('strengths', [])
                areas_for_improvement = result.get('areas_for_improvement', [])
                
                detailed_feedback = f"{feedback}\n\nJustification:\n{justification}\n\nStrengths:\n"
                for i, strength in enumerate(strengths, 1):
                    detailed_feedback += f"- {strength}\n"
                
                detailed_feedback += "\nAreas for improvement:\n"
                for i, area in enumerate(areas_for_improvement, 1):
                    detailed_feedback += f"- {area}\n"
                
                # Include the point description that matches the assigned points
                if str(points) in point_descriptions:
                    detailed_feedback += f"\nRubric level achieved: {points} points - {point_descriptions[str(points)]}"
                
                grades.append({
                    'criterion_name': criterion['name'],
                    'max_points': criterion['max_points'],
                    'points': points,
                    'feedback': detailed_feedback
                })
            except json.JSONDecodeError:
                # Fallback in case of invalid JSON
                default_points = max(1, criterion['max_points'] // 2)  # Default to middle points
                grades.append({
                    'criterion_name': criterion['name'],
                    'max_points': criterion['max_points'],
                    'points': default_points,
                    'feedback': f"The essay shows some understanding of {criterion['name'].lower()}, but has room for improvement."
                })
                
        except Exception as e:
            # Handle any errors with API or processing
            print(f"Error grading criterion '{criterion['name']}': {str(e)}")
            default_points = max(1, criterion['max_points'] // 2)  # Default to middle points
            grades.append({
                'criterion_name': criterion['name'],
                'max_points': criterion['max_points'],
                'points': default_points,
                'feedback': f"The essay shows some understanding of {criterion['name'].lower()}, but has room for improvement."
            })
    
    return grades

def analyze_essay_overview(essay_text: str, grades: List[Dict]) -> str:
    """
    Generate an overall feedback summary based on the essay and individual grades.
    
    Args:
        essay_text: The text of the essay.
        grades: List of grade dictionaries with points and feedback.
        
    Returns:
        A string with overall feedback.
    """
    # Calculate total points and percentage
    total_points = sum(grade['points'] for grade in grades)
    max_points = sum(grade['max_points'] for grade in grades)
    percentage = (total_points / max_points) * 100 if max_points > 0 else 0
    
    # Create a summary of the grades
    grade_summary = "\n".join([
        f"- {grade['criterion_name']}: {grade['points']}/{grade['max_points']} points" 
        for grade in grades
    ])
    
    # Create a prompt for the overall analysis
    prompt = f"""
    You are an experienced teacher providing overall feedback on an essay. The essay has been graded with the following scores:
    
    {grade_summary}
    
    Total score: {total_points}/{max_points} ({percentage:.1f}%)
    
    Based on the essay, provide a concise, constructive overall feedback paragraph (about 3-5 sentences) that summarizes the essay's strengths and weaknesses,
    and offers guidance for improvement. Focus on being encouraging while highlighting key areas for development.
    
    ESSAY:
    {essay_text}
    """
    
    try:
        response = model.generate_content(prompt)
        overall_feedback = response.text.strip()
        return overall_feedback
    except Exception as e:
        print(f"Error generating overall feedback: {str(e)}")
        # Provide a default feedback if LLM fails
        if percentage >= 80:
            return "Excellent work! The essay demonstrates thorough understanding of the topic and effectively addresses most requirements. Consider minor refinements for future assignments."
        elif percentage >= 60:
            return "Good effort! The essay shows a basic understanding of the subject. Focus on developing stronger arguments and providing more detailed analysis in future assignments."
        else:
            return "This essay needs significant improvement. Focus on addressing the key requirements of each criterion and developing your ideas more thoroughly."
